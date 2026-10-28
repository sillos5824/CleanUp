from datetime import datetime, timedelta
import io
import json
import sqlite3

import folium
from PIL import Image, ImageDraw, ImageFont
import pandas as pd
import qrcode
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from streamlit_searchbox import st_searchbox
from streamlit_option_menu import option_menu  # Pour une navigation moderne
import streamlit.components.v1 as components

# ============================================================
# CONFIGURATION DE LA PAGE & DESIGN CSS GLOBAL
# ============================================================

st.set_page_config(
    page_title="CleanUp+ | Gestion Pressing",
    page_icon="🧺",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Injection de styles CSS avancés (incluant l'animation de clignotement)
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Plus Jakarta+ Sans', sans-serif;
        }

        .stApp {
            background-color: #f8fafc;
        }

        /* Animation de clignotement pour les alertes */
        @keyframes clignoter {
            0% { opacity: 1; }
            50% { opacity: 0.2; }
            100% { opacity: 1; }
        }
        .alerte-clignotante {
            animation: clignoter 1s infinite;
            color: #ef4444;
            font-weight: bold;
        }

        /* Boutons stylisés */
        .stButton>button {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            border-radius: 8px;
            padding: 0.5rem 1rem;
            font-weight: 600;
            border: none;
            box-shadow: 0 4px 6px -1px rgba(30, 60, 114, 0.2);
            transition: all 0.3s ease;
        }
        .stButton>button:hover {
            background: linear-gradient(135deg, #2a5298 0%, #1e3c72 100%);
            box-shadow: 0 6px 12px -2px rgba(30, 60, 114, 0.3);
            transform: translateY(-1px);
        }

        /* Champs de saisie homogènes */
        .stTextInput>div>div>input, .stSelectbox>div>div>select, .stTextArea>div>div>textarea {
            border-radius: 8px;
            border: 1px solid #cbd5e1;
            background-color: #ffffff;
        }
    </style>
""", unsafe_allow_html=True)

DB_NAME = "pressing.db"
NUMERO_WAVE = "0140994610"


# ============================================================
# GESTION DE LA BASE DE DONNÉES & AUDIO
# ============================================================

def get_connection():
    return sqlite3.connect(DB_NAME)


def jouer_son_notification():
    son_url = "https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3"
    sound_html = f"""
        <audio autoplay>
            <source src="{son_url}" type="audio/mp3">
        </audio>
    """
    components.html(sound_html, height=0, width=0)


def init_db():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS commandes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_depot TEXT,
                nom_client TEXT,
                telephone_client TEXT,
                genre_client TEXT,
                articles_deposes TEXT,
                articles_recuperes TEXT,
                prix_total REAL,
                modalite_paiement TEXT,
                mode_paiement TEXT DEFAULT 'Espèces',
                montant_verse REAL,
                statut_traitement TEXT,
                quantite_restante_traitement INTEGER,
                date_recuperation TEXT,
                statut_commande TEXT,
                dans_corbeille INTEGER DEFAULT 0,
                latitude REAL,
                longitude REAL,
                adresse_livraison TEXT,
                type_commande TEXT DEFAULT 'Comptoir',
                service_choisi TEXT DEFAULT 'Lavage + Repassage'
            )
        """)
        c.execute("PRAGMA table_info(commandes)")
        columns = [column[1] for column in c.fetchall()]

        migrations = [
            ("telephone_client", "TEXT DEFAULT ''"),
            ("latitude", "REAL"),
            ("longitude", "REAL"),
            ("adresse_livraison", "TEXT"),
            ("type_commande", "TEXT DEFAULT 'Comptoir'"),
            ("service_choisi", "TEXT DEFAULT 'Lavage + Repassage'"),
            ("mode_paiement", "TEXT DEFAULT 'Espèces'")
        ]

        for col_name, col_type in migrations:
            if col_name not in columns:
                try:
                    c.execute(f"ALTER TABLE commandes ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass

        c.execute("""
            CREATE TABLE IF NOT EXISTS depenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date_depense TEXT,
                designation TEXT,
                montant REAL,
                commentaire TEXT
            )
        """)
        conn.commit()


init_db()

# ============================================================
# GRILLE TARIFAIRE OFFICIELLE & CALCUL DU DEVIS
# ============================================================

TARIFS_BASE = {
    "Chemise / Polo": 300.0, "T-shirt": 250.0, "Pantalon / Jean": 400.0, "Jupe": 350.0, "Robe simple": 500.0,
    "Robe de cérémonie": 1500.0, "Robe longue / Robe en pagne": 1000.0, "Costume complet (veste + pantalon)": 2000.0,
    "Veste seule": 1000.0, "Blazer": 1200.0, "Boubou simple": 800.0, "Boubou brodé / Grand boubou": 1500.0,
    "Tissu pagne (2 m)": 500.0, "Tissu pagne (3 m)": 700.0, "Drap simple": 700.0, "Drap 2 places": 1000.0,
    "Drap + taies (set)": 1200.0, "Housse de couette": 1500.0, "Couette légère": 2500.0, "Couette épaisse": 3500.0,
    "Rideaux légers (la paire)": 1500.0, "Rideaux lourds (la paire)": 2500.0,
}

vetements_ordinaires = ["Chemise / Polo", "T-shirt", "Pantalon / Jean", "Jupe", "Robe simple"]
vetements_speciaux = ["Robe de cérémonie", "Robe longue / Robe en pagne", "Costume complet (veste + pantalon)",
                      "Veste seule", "Blazer", "Boubou simple", "Boubou brodé / Grand boubou", "Tissu pagne (2 m)",
                      "Tissu pagne (3 m)"]
linge_maison = ["Drap simple", "Drap 2 places", "Drap + taies (set)", "Housse de couette", "Couette légère",
                "Couette épaisse", "Rideaux légers (la paire)", "Rideaux lourds (la paire)"]


def calculer_devis(articles_selectionnes: dict, service: str) -> float:
    total = 0.0
    for article, qte in articles_selectionnes.items():
        if qte > 0 and article in TARIFS_BASE:
            prix_base = TARIFS_BASE[article]
            total += prix_base * qte * (1.0 if service == "Lavage + Repassage" else 0.75)
    return float(total)


def generer_qr_code_bytes(data_url: str, type_panneau: str = "comptoir") -> bytes:
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    width, height = 800, 1100
    panel = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(panel)

    if type_panneau == "comptoir":
        primary_color = (255, 140, 0)
        btn_text = "AU COMPTOIR"
        sub_text = "Scannez pour commander au comptoir"
    else:
        primary_color = (26, 150, 225)
        btn_text = "EN LIGNE"
        sub_text = "Scannez pour commander en ligne"

    text_dark = (15, 23, 42)
    draw.rounded_rectangle([30, 30, width - 30, height - 30], radius=40, outline=primary_color, width=6)

    try:
        font_title = ImageFont.truetype("arial.ttf", 55)
        font_btn = ImageFont.truetype("arial.ttf", 40)
        font_sub = ImageFont.truetype("arial.ttf", 22)
    except IOError:
        font_title = ImageFont.load_default()
        font_btn = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    draw.text((width / 2 - 140, 95), "CleanUp", fill=text_dark, font=font_title)
    draw.text((width / 2 + 105, 95), "+", fill=primary_color, font=font_title)
    draw.rectangle([width / 2 - 80, 165, width / 2 + 80, 173], fill=primary_color)

    qr_size = 480
    qr_img = qr_img.resize((qr_size, qr_size))
    qr_x = (width - qr_size) // 2
    qr_y = 210

    draw.rounded_rectangle([qr_x - 20, qr_y - 20, qr_x + qr_size + 20, qr_y + qr_size + 20], radius=25,
                           outline=primary_color, width=4)
    panel.paste(qr_img, (qr_x, qr_y))

    btn_x, btn_y, btn_w, btn_h = 160, 750, 480, 90
    draw.rounded_rectangle([btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], radius=45, fill=primary_color)
    bbox = draw.textbbox((0, 0), btn_text, font=font_btn)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((btn_x + (btn_w - tw) / 2, btn_y + (btn_h - th) / 2 - 5), btn_text, fill="white", font=font_btn)

    bbox_sub = draw.textbbox((0, 0), sub_text, font=font_sub)
    sw = bbox_sub[2] - bbox_sub[0]
    draw.text(((width - sw) / 2, 880), sub_text, fill=(100, 116, 139), font=font_sub)

    buf = io.BytesIO()
    panel.save(buf, format="PNG")
    return buf.getvalue()


def obtenir_nb_nouvelles_commandes():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM commandes WHERE statut_commande = 'En cours' AND dans_corbeille = 0")
        return c.fetchone()[0]


def search_adresse_autocomplete(searchterm: str):
    if not searchterm or len(searchterm.strip()) < 2:
        return []
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{searchterm}, Abidjan, Côte d'Ivoire", "format": "json", "limit": 8, "addressdetails": 1,
              "countrycodes": "ci"}
    headers = {"User-Agent": "PressingAppStreamlit/1.0"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=4)
        if response.status_code == 200:
            return [(item["display_name"], item) for item in response.json()]
    except Exception:
        pass
    return []


DESIGNATIONS_DEPENSES = ["Salaire du gérant", "Achat des intrants (produits lavage)", "Facture CIE", "Facture SODECI",
                         "Autre dépense"]
SERVICES = ["Lavage + Repassage", "Lavage simple", "Repassage simple"]
MODALITES_PAIEMENT = ["Soldé", "Acompte"]
MODES_PAIEMENT = ["Espèces", "Wave"]

# ============================================================
# NAVIGATION & ROUTAGE URL
# ============================================================

query_params = st.query_params
cmd_id_url = query_params.get("cmd_id", None)
page_url = query_params.get("page", None)

with st.sidebar:
    st.image("https://img.icons8.com/color/96/basket.png", width=65)
    st.title("CleanUp+")
    st.caption("Gestion intelligente de pressing")
    st.divider()

if cmd_id_url or page_url == "ligne":
    with st.sidebar:
        st.info("📱 Espace Client Dédié")
    menu = "📱 Commande en Ligne (Client)"
elif page_url == "comptoir":
    with st.sidebar:
        st.info("🏬 Espace Comptoir Dédié")
    menu = "Nouvelle Commande (Comptoir)"
else:
    # Bannière Header élégante pour l'accueil Admin
    st.markdown("""
        <div style="background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); padding: 30px; border-radius: 14px; color: white; text-align: center; box-shadow: 0 10px 25px -5px rgba(30, 60, 114, 0.2); margin-bottom: 25px;">
            <h1 style="color: white; margin: 0; font-size: 2.5em; font-weight: 700;">🧺 CleanUp+</h1>
            <p style="font-size: 1.1em; margin-top: 8px; opacity: 0.95;">Tableau de bord et pilotage de votre activité</p>
        </div>
    """, unsafe_allow_html=True)

    nb_nouvelles = obtenir_nb_nouvelles_commandes()
    badge_inbox = f"Boîte de Réception ({nb_nouvelles})" if nb_nouvelles > 0 else "Boîte de Réception"

    with st.sidebar:
        st.markdown("### 🛠️ Navigation Principale")
        menu = option_menu(
            menu_title=None,
            options=[
                badge_inbox,
                "Nouvelle Commande (Comptoir)",
                "📱 Commande en Ligne (Client)",
                "Mise à jour & Retraits",
                "Historique des Commandes",
                "💸 Dépenses & Bénéfice",
                "📲 Générer QR Code",
                "🗑 Corbeille"
            ],
            icons=["inbox", "plus-circle", "phone", "arrow-repeat", "clock-history", "cash-coin", "qr-code", "trash"],
            default_index=0,
            styles={
                "container": {"padding": "0!important", "background-color": "transparent"},
                "icon": {"color": "#1e3c72", "font-size": "15px"},
                "nav-link": {"font-size": "14px", "text-align": "left", "margin": "0px", "--hover-color": "#e2e8f0"},
                "nav-link-selected": {"background-color": "#1e3c72", "color": "white"}
            }
        )
        if nb_nouvelles > 0:
            # Alerte visuelle clignotante dans la barre latérale
            st.markdown(f'<p class="alerte-clignotante">🔔 {nb_nouvelles} commande(s) en attente !</p>', unsafe_allow_html=True)
            # Déclenchement du signal sonore
            jouer_son_notification()

# ============================================================
# 1. BOÎTE DE RÉCEPTION
# ============================================================

if "Boîte de Réception" in menu:
    st.header("📥 Boîte de Réception des Commandes")

    with get_connection() as conn:
        df_recus = pd.read_sql_query(
            "SELECT * FROM commandes WHERE statut_commande = 'En cours' AND dans_corbeille = 0 ORDER BY id DESC", conn)

    if df_recus.empty:
        st.success("🎉 Aucune nouvelle commande en attente de traitement !")
    else:
        st.info(f"📌 Vous avez **{len(df_recus)}** commande(s) active(s) à traiter.")
        filtre_type = st.radio("Filtrer par origine :", ["Toutes", "En ligne", "Comptoir"], horizontal=True)

        if filtre_type != "Toutes":
            df_recus = df_recus[df_recus["type_commande"] == filtre_type]

        for _, row in df_recus.iterrows():
            badge_type = "📱 EN LIGNE" if row["type_commande"] == "En ligne" else "🏬 COMPTOIR"

            with st.container(border=True):
                st.subheader(f"Commande #{row['id']} — {row['nom_client']} ({badge_type})")
                col_a, col_b = st.columns(2)

                with col_a:
                    st.write(f"📞 **Téléphone :** {row['telephone_client']}")
                    st.write(f"⚙️ **Service :** {row.get('service_choisi', 'Lavage + Repassage')}")
                    if row["type_commande"] == "En ligne":
                        st.write(f"📍 **Adresse :** {row['adresse_livraison'] or 'Non précisée'}")
                    st.write(f"📅 **Date :** {row['date_depot']}")

                with col_b:
                    st.write(f"💰 **Prix total :** {row['prix_total']:,} FCFA")
                    st.write(f"🏷️ **Modalité :** {row['modalite_paiement']} ({row['montant_verse']:,} versé)")
                    st.write(f"💳 **Paiement :** {row.get('mode_paiement', 'Espèces')}")
                    if row.get('mode_paiement') == 'Wave':
                        st.info(f"📱 Compte Wave : **{NUMERO_WAVE}**")

                with st.expander("🛒 Voir le détail des articles"):
                    try:
                        arts = json.loads(row["articles_deposes"])
                        for item, qte in arts.items():
                            st.write(f"- {item} : **{qte}**")
                    except Exception:
                        st.write(row["articles_deposes"])

                btn_val, btn_term = st.columns(2)
                with btn_val:
                    if st.button(f"✅ Passer en traitement (N°{row['id']})", key=f"traite_{row['id']}"):
                        with get_connection() as conn:
                            c = conn.cursor()
                            c.execute("UPDATE commandes SET statut_traitement = 'En cours de lavage' WHERE id = ?",
                                      (row['id'],))
                            conn.commit()
                        st.toast(f"Commande #{row['id']} en cours de traitement !")
                        st.rerun()

                with btn_term:
                    if st.button(f"🏁 Marquer comme Terminée (N°{row['id']})", key=f"fin_{row['id']}"):
                        with get_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE commandes SET statut_commande = 'Terminées', statut_traitement = 'Terminées' WHERE id = ?",
                                (row['id'],))
                            conn.commit()
                        st.toast(f"Commande #{row['id']} clôturée !")
                        st.rerun()

# ============================================================
# 2. NOUVELLE COMMANDE - COMPTOIR
# ============================================================

elif menu == "Nouvelle Commande (Comptoir)":
    st.header("📝 Enregistrer une nouvelle commande (Comptoir)")

    with st.container(border=True):
        st.subheader("1. Informations Client & Paiement")
        col1, col2 = st.columns(2)
        with col1:
            nom_client = st.text_input("Nom du client *")
            telephone_client = st.text_input("Numéro de téléphone *")
            genre_client = st.selectbox("Genre du client", ["Homme", "Femme", "Autre"])
            date_depot = st.date_input("Date de dépôt", datetime.now())
        with col2:
            service_choisi = st.selectbox("Type de Service *", SERVICES)
            modalite = st.radio("Modalité de paiement *", MODALITES_PAIEMENT, horizontal=True)
            mode_paiement = st.selectbox("Mode de paiement *", MODES_PAIEMENT)

        if mode_paiement == "Wave":
            st.info(f"📱 Effectuer le transfert Wave au : **{NUMERO_WAVE}**")

    st.subheader("2. Grille Tarifaire & Sélection des Articles")
    articles_selectionnes = {}

    tab_o, tab_s, tab_m = st.tabs(["👔 Vêtements Ordinaires", "👗 Vêtements Spéciaux", "🛏️ Linge de Maison"])

    with tab_o:
        cols = st.columns(3)
        for idx, item in enumerate(vetements_ordinaires):
            with cols[idx % 3]:
                qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1, key=f"d_ord_{item}")
                if qte > 0: articles_selectionnes[item] = qte

    with tab_s:
        cols = st.columns(3)
        for idx, item in enumerate(vetements_speciaux):
            with cols[idx % 3]:
                qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1, key=f"d_spe_{item}")
                if qte > 0: articles_selectionnes[item] = qte

    with tab_m:
        cols = st.columns(3)
        for idx, item in enumerate(linge_maison):
            with cols[idx % 3]:
                qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1, key=f"d_lin_{item}")
                if qte > 0: articles_selectionnes[item] = qte

    total_articles = sum(articles_selectionnes.values())
    prix_calcule = calculer_devis(articles_selectionnes, service_choisi)

    with st.container(border=True):
        st.subheader("💰 Récapitulatif & Validation")
        st.markdown(f"**Total articles :** {total_articles} | **Service :** {service_choisi}")
        st.markdown(f"### Montant total : :green[{prix_calcule:,.0f} FCFA]")

        if modalite == "Acompte":
            montant_verse_input = st.number_input("Montant versé (FCFA) *", min_value=0.0,
                                                  max_value=float(prix_calcule) if prix_calcule > 0 else 0.0, value=0.0,
                                                  step=100.0)
        else:
            montant_verse_input = prix_calcule

        if st.button("Valider la commande", type="primary"):
            montant_verse = prix_calcule if modalite == "Soldé" else montant_verse_input
            if not nom_client.strip():
                st.error("❌ Le nom du client est obligatoire.")
            elif not telephone_client.strip():
                st.error("❌ Le téléphone est obligatoire.")
            elif total_articles == 0:
                st.error("❌ Sélectionnez au moins un article.")
            elif modalite == "Acompte" and montant_verse <= 0:
                st.error("❌ Précisez un acompte valide.")
            elif modalite == "Acompte" and montant_verse >= prix_calcule:
                st.error("❌ L'acompte ne peut pas être supérieur ou égal au total (choisissez 'Soldé').")
            else:
                articles_recup_initial = {item: 0 for item in articles_selectionnes}
                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO commandes (
                            date_depot, nom_client, telephone_client, genre_client,
                            articles_deposes, articles_recuperes, prix_total, modalite_paiement,
                            mode_paiement, montant_verse, statut_traitement, quantite_restante_traitement,
                            date_recuperation, statut_commande, dans_corbeille, latitude,
                            longitude, adresse_livraison, type_commande, service_choisi
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, 'Comptoir', ?)
                    """, (
                        str(date_depot), nom_client, telephone_client, genre_client,
                        json.dumps(articles_selectionnes), json.dumps(articles_recup_initial),
                        prix_calcule, modalite, mode_paiement, montant_verse, "En cours", total_articles,
                        "-", "En cours", service_choisi
                    ))
                    new_id = c.lastrowid
                    conn.commit()

                st.success(f"✅ Commande #{new_id} validée pour un montant de {prix_calcule:,.0f} FCFA.")
                st.balloons()

# ============================================================
# 3. COMMANDE EN LIGNE (CLIENT)
# ============================================================

elif menu == "📱 Commande en Ligne (Client)":
    if cmd_id_url:
        st.header(f"🔍 Suivi de la Commande #{cmd_id_url}")
        with get_connection() as conn:
            df_view = pd.read_sql_query("SELECT * FROM commandes WHERE id = ?", conn, params=(cmd_id_url,))

        if not df_view.empty:
            cmd = df_view.iloc[0]
            with st.container(border=True):
                st.success(f"Statut : **{cmd['statut_commande']}**")
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Client :** {cmd['nom_client']}")
                    st.write(f"**Téléphone :** {cmd['telephone_client']}")
                    st.write(f"**Date :** {cmd['date_depot']}")
                with c2:
                    st.write(f"**Service :** {cmd.get('service_choisi', 'Lavage + Repassage')}")
                    st.write(f"**Total :** {cmd['prix_total']:,} FCFA")
                    st.write(f"**Paiement :** {cmd.get('mode_paiement', 'Espèces')}")

                st.subheader("Articles commandés")
                try:
                    for art, qte in json.loads(cmd["articles_deposes"]).items():
                        st.write(f"- {art} : **{qte}**")
                except Exception:
                    st.write(cmd["articles_deposes"])
        else:
            st.error("❌ Commande introuvable.")
    else:
        # --- CONSERVATION UNIQUE DE L'IMAGE DE LA FAMILLE HEUREUSE ---
        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            st.markdown("### Bienvenue sur votre espace client")
            st.write("Passez votre commande en ligne et profitez d'un linge impeccable sans vous déplacer.")
        with col_c2:
            st.image(
                "https://images.unsplash.com/photo-1511895426328-dc8714191300?q=80&w=1200&auto=format&fit=crop",
                caption="Le bonheur d'un linge impeccable en famille !",
                use_container_width=True
            )

        st.header("📱 Passation de commande en ligne")

        if "user_lat" not in st.session_state: st.session_state.user_lat = 5.3599517
        if "user_lng" not in st.session_state: st.session_state.user_lng = -4.0082563

        with st.container(border=True):
            st.subheader("📍 1. Localisation de livraison (Abidjan)")
            col_m1, col_m2 = st.columns([1, 2])
            with col_m1:
                if st.button("🌐 Obtenir ma position GPS"):
                    loc = get_geolocation()
                    if loc and "coords" in loc:
                        st.session_state.user_lat = loc["coords"]["latitude"]
                        st.session_state.user_lng = loc["coords"]["longitude"]
                        st.success("Position actualisée !")

                selected_adresse_data = st_searchbox(
                    search_adresse_autocomplete,
                    key="adresse_searchbox",
                    placeholder="Commune, quartier ou rue...",
                    label="Recherche rapide d'adresse"
                )
                adresse_finale = selected_adresse_data["display_name"] if selected_adresse_data else st.text_input(
                    "Précisions (Bâtiment, porte...)")
                if selected_adresse_data:
                    st.session_state.user_lat = float(selected_adresse_data["lat"])
                    st.session_state.user_lng = float(selected_adresse_data["lon"])

            with col_m2:
                m = folium.Map(location=[st.session_state.user_lat, st.session_state.user_lng], zoom_start=14)
                folium.Marker([st.session_state.user_lat, st.session_state.user_lng], popup="Lieu de livraison").add_to(
                    m)
                map_data = st_folium(m, height=270, width="100%", key="map_client")
                if map_data and map_data.get("last_clicked"):
                    st.session_state.user_lat = map_data["last_clicked"]["lat"]
                    st.session_state.user_lng = map_data["last_clicked"]["lng"]

        with st.container(border=True):
            st.subheader("📋 2. Informations & Prestation")
            c1, c2 = st.columns(2)
            with c1:
                nom_client = st.text_input("Nom & Prénom *")
                telephone_client = st.text_input("Téléphone *")
                genre_client = st.selectbox("Genre", ["Homme", "Femme", "Autre"])
            with c2:
                service_choisi = st.selectbox("Prestation *", SERVICES)
                modalite_client = st.radio("Modalité *", MODALITES_PAIEMENT, horizontal=True)
                mode_paiement_client = st.selectbox("Mode de paiement *", MODES_PAIEMENT)

            if mode_paiement_client == "Wave":
                st.warning(f"📱 Paiement Wave à effectuer au : **{NUMERO_WAVE}**")

        st.subheader("🛒 3. Sélection des articles")
        articles_client = {}
        tab_oc, tab_sc, tab_mc = st.tabs(["👔 Vêtements Ordinaires", "👗 Vêtements Spéciaux", "🛏️ Linge de Maison"])

        with tab_oc:
            cols = st.columns(3)
            for idx, item in enumerate(vetements_ordinaires):
                with cols[idx % 3]:
                    qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1,
                                          key=f"on_ord_{item}")
                    if qte > 0: articles_client[item] = qte
        with tab_sc:
            cols = st.columns(3)
            for idx, item in enumerate(vetements_speciaux):
                with cols[idx % 3]:
                    qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1,
                                          key=f"on_spe_{item}")
                    if qte > 0: articles_client[item] = qte
        with tab_mc:
            cols = st.columns(3)
            for idx, item in enumerate(linge_maison):
                with cols[idx % 3]:
                    qte = st.number_input(f"{item} ({TARIFS_BASE[item]:,.0f} F)", min_value=0, step=1,
                                          key=f"on_lin_{item}")
                    if qte > 0: articles_client[item] = qte

        total_art = sum(articles_client.values())
        prix_estime = calculer_devis(articles_client, service_choisi)

        if total_art > 0:
            st.info(f"💡 Devis estimé : **{prix_estime:,.0f} FCFA** ({total_art} article(s))")

        if modalite_client == "Acompte":
            montant_verse_client = st.number_input("Montant versé (FCFA) *", min_value=0.0,
                                                   max_value=float(prix_estime) if prix_estime > 0 else 0.0, value=0.0,
                                                   step=100.0, key="acompte_client")
        else:
            montant_verse_client = prix_estime

        if st.button("Envoyer la commande en ligne", type="primary"):
            if not nom_client.strip():
                st.error("❌ Le nom est obligatoire.")
            elif not telephone_client.strip():
                st.error("❌ Le téléphone est obligatoire.")
            elif total_art == 0:
                st.error("❌ Sélectionnez au moins un article.")
            else:
                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO commandes (
                            date_depot, nom_client, telephone_client, genre_client,
                            articles_deposes, articles_recuperes, prix_total, modalite_paiement,
                            mode_paiement, montant_verse, statut_traitement, quantite_restante_traitement,
                            date_recuperation, statut_commande, dans_corbeille, latitude,
                            longitude, adresse_livraison, type_commande, service_choisi
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'En cours', ?, '-', 'En cours', 0, ?, ?, ?, 'En ligne', ?)
                    """, (
                        datetime.now().strftime("%Y-%m-%d"), nom_client, telephone_client, genre_client,
                        json.dumps(articles_client), json.dumps({k: 0 for k in articles_client}), prix_estime,
                        modalite_client, mode_paiement_client, montant_verse_client, total_art,
                        st.session_state.user_lat, st.session_state.user_lng, adresse_finale, service_choisi
                    ))
                    last_id = c.lastrowid
                    conn.commit()

                st.success(f"✅ Commande #{last_id} enregistrée avec succès !")
                base_url = "https://cleanup-dyf9esw7bawavodkfst36v.streamlit.app"
                byte_qr = generer_qr_code_bytes(f"{base_url}/?cmd_id={last_id}", type_panneau="ligne")
                st.image(byte_qr, caption=f"QR Code Commande #{last_id}", width=300)

# ============================================================
# 4. MISE À JOUR & RETRAITS
# ============================================================

elif menu == "Mise à jour & Retraits":
    st.header("📦 Mise à jour & Retraits des articles")

    with get_connection() as conn:
        df = pd.read_sql_query("SELECT * FROM commandes WHERE dans_corbeille = 0 ORDER BY id DESC", conn)

    if df.empty:
        st.info("Aucune commande disponible.")
    else:
        commande_id = st.selectbox(
            "Sélectionner une commande :",
            df["id"].tolist(),
            format_func=lambda
                x: f"Commande #{x} - {df[df['id'] == x]['nom_client'].values[0]} ({df[df['id'] == x]['type_commande'].values[0]})"
        )
        commande = df[df["id"] == commande_id].iloc[0]

        prix_tot = float(commande['prix_total'] or 0.0)
        montant_v = float(commande['montant_verse'] or 0.0)
        reste_a_payer = max(0.0, prix_tot - montant_v)

        with st.container(border=True):
            col_info1, col_info2 = st.columns(2)
            with col_info1:
                st.markdown(f"👤 **Client :** {commande['nom_client']}")
                st.markdown(f"📞 **Téléphone :** {commande['telephone_client']}")
                st.markdown(f"📅 **Date dépôt :** {commande['date_depot']}")
                st.markdown(f"📱 **Origine :** {commande['type_commande']}")
                if commande['type_commande'] == 'En ligne' and commande['adresse_livraison']:
                    st.markdown(f"📍 **Adresse :** {commande['adresse_livraison']}")
            with col_info2:
                st.markdown(f"💳 **Prix Total :** {prix_tot:,.0f} FCFA")
                st.markdown(f"💵 **Déjà versé :** {montant_v:,.0f} FCFA")
                st.markdown(f"📌 **Reste à payer :** :green[{reste_a_payer:,.0f} FCFA]")
                st.markdown(f"🔄 **Statut :** `:green[{commande['statut_commande']}]`")

        if commande['type_commande'] == 'En ligne' and pd.notna(commande.get('latitude')) and pd.notna(
                commande.get('longitude')):
            lat_c, lon_c = float(commande['latitude']), float(commande['longitude'])
            google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat_c},{lon_c}"
            st.markdown(
                f"""<a href="{google_maps_url}" target="_blank" style="display:inline-block; background-color:#2a5298; color:white; padding:8px 16px; border-radius:6px; text-decoration:none; font-weight:bold; margin-bottom:12px;">🗺️ Ouvrir l'itinéraire client dans Google Maps</a>""",
                unsafe_allow_html=True)
            m_client = folium.Map(location=[lat_c, lon_c], zoom_start=15)
            folium.Marker([lat_c, lon_c], popup=f"Client: {commande['nom_client']}",
                          icon=folium.Icon(color="blue", icon="user")).add_to(m_client)
            st_folium(m_client, height=250, width="100%", key=f"map_retrait_{commande_id}")

        with st.container(border=True):
            st.subheader("🧺 1. Vérification des articles retirés")
            try:
                articles_deposes = json.loads(commande["articles_deposes"])
            except:
                articles_deposes = {}
            try:
                articles_recuperes = json.loads(commande["articles_recuperes"])
            except:
                articles_recuperes = {k: 0 for k in articles_deposes}

            nouveaux_recup = {}
            cols_art = st.columns(3)
            for idx, (item, qte_dep) in enumerate(articles_deposes.items()):
                qte_deja_recup = articles_recuperes.get(item, 0)
                with cols_art[idx % 3]:
                    nouveaux_recup[item] = st.number_input(f"{item} (Déposés: {qte_dep} | Retirés)",
                                                           min_value=qte_deja_recup, max_value=qte_dep,
                                                           value=qte_deja_recup, key=f"ret_{commande_id}_{item}")

            st.subheader("⚙️ 2. Statut & Paiement")
            col_st1, col_st2, col_st3, col_st4 = st.columns(4)
            with col_st1:
                nouveau_statut = st.selectbox("Statut", ["En cours", "Terminées"],
                                              index=0 if commande["statut_commande"] == "En cours" else 1)
            with col_st2:
                nouvelle_modalite = st.selectbox("Modalité", MODALITES_PAIEMENT, index=0 if commande[
                                                                                                "modalite_paiement"] not in MODALITES_PAIEMENT else MODALITES_PAIEMENT.index(
                    commande["modalite_paiement"]))
            with col_st3:
                nouveau_mode = st.selectbox("Mode paiement", MODES_PAIEMENT, index=0)
            with col_st4:
                nouveau_montant_verse = st.number_input("Montant versé", min_value=0.0, max_value=prix_tot,
                                                        value=montant_v,
                                                        step=100.0) if nouvelle_modalite == "Acompte" else prix_tot

            if st.button("💾 Enregistrer les modifications", type="primary"):
                tot_dep, tot_rec = sum(articles_deposes.values()), sum(nouveaux_recup.values())
                quantite_restante = max(0, tot_dep - tot_rec)
                date_retrait_str = datetime.now().strftime("%Y-%m-%d %H:%M") if quantite_restante == 0 else commande[
                    "date_recuperation"]

                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute("""
                        UPDATE commandes
                        SET articles_recuperes = ?, statut_commande = ?, statut_traitement = ?,
                            modalite_paiement = ?, mode_paiement = ?, montant_verse = ?,
                            quantite_restante_traitement = ?, date_recuperation = ?
                        WHERE id = ?
                    """, (json.dumps(nouveaux_recup), nouveau_statut, nouveau_statut, nouvelle_modalite, nouveau_mode,
                          nouveau_montant_verse, quantite_restante, date_retrait_str, commande_id))
                    conn.commit()
                st.success("✅ Modifications enregistrées avec succès !")
                st.rerun()

# ============================================================
# 5. HISTORIQUE DES COMMANDES
# ============================================================

elif menu == "Historique des Commandes":
    col_h1, col_h2, col_h3 = st.columns([3, 1, 1])
    with col_h1:
        st.header("📊 Historique des Commandes")
    with col_h2:
        if st.button("🔄 Actualiser", use_container_width=True): st.rerun()
    with col_h3:
        if st.button("🗑️ Vider", type="secondary", use_container_width=True): st.session_state.confirmation_raz = True

    if st.session_state.get("confirmation_raz", False):
        st.warning("⚠️ Voulez-vous archiver toutes les commandes actives vers la corbeille ?")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Oui, archiver"):
                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE commandes SET dans_corbeille = 1 WHERE dans_corbeille = 0")
                    conn.commit()
                st.session_state.confirmation_raz = False
                st.rerun()
        with c2:
            if st.button("❌ Annuler"):
                st.session_state.confirmation_raz = False
                st.rerun()

    filtre = st.radio("Filtrer", ["Toutes", "En cours", "Terminées"], horizontal=True)
    with get_connection() as conn:
        df = pd.read_sql_query(
            "SELECT * FROM commandes WHERE dans_corbeille = 0 ORDER BY id DESC" if filtre == "Toutes" else "SELECT * FROM commandes WHERE statut_commande = ? AND dans_corbeille = 0 ORDER BY id DESC",
            conn, params=None if filtre == "Toutes" else (filtre,))

    if df.empty:
        st.info("Aucune commande enregistrée.")
    else:
        df_display = df.copy()
        df_display["Articles Déposés"] = df_display["articles_deposes"].apply(
            lambda val: ", ".join([f"{k}: {v}" for k, v in json.loads(val).items()]) if pd.notna(val) else "")
        st.dataframe(df_display[
                         ["id", "date_depot", "type_commande", "nom_client", "telephone_client", "service_choisi",
                          "Articles Déposés", "prix_total", "modalite_paiement", "statut_commande"]],
                     use_container_width=True)

# ============================================================
# 6. DÉPENSES & BÉNÉFICE
# ============================================================

elif menu == "💸 Dépenses & Bénéfice":
    st.header("💸 Gestion des Dépenses & Bénéfice")
    with st.container(border=True):
        with st.form("form_depense"):
            c1, c2 = st.columns(2)
            with c1:
                date_depense = st.date_input("Date", datetime.now())
                designation = st.selectbox("Désignation", DESIGNATIONS_DEPENSES)
            with c2:
                montant_depense = st.number_input("Montant (FCFA)", min_value=0.0, step=100.0)
                commentaire = st.text_input("Commentaire optionnel")
            ajouter_depense = st.form_submit_button("Ajouter la dépense")

    if ajouter_depense and montant_depense > 0:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT INTO depenses (date_depense, designation, montant, commentaire) VALUES (?, ?, ?, ?)",
                      (str(date_depense), designation, montant_depense, commentaire))
            conn.commit()
        st.success("Dépense enregistrée avec succès.")
        st.rerun()

    with get_connection() as conn:
        total_commandes = \
            pd.read_sql_query("SELECT COALESCE(SUM(prix_total), 0) AS total FROM commandes WHERE dans_corbeille = 0",
                              conn).iloc[0]["total"]
        total_depenses = pd.read_sql_query("SELECT COALESCE(SUM(montant), 0) AS total FROM depenses", conn).iloc[0][
            "total"]

    benefice = total_commandes - total_depenses
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("💰 Chiffre d'affaires", f"{total_commandes:,.0f} FCFA")
    with c2:
        st.metric("💸 Total dépenses", f"{total_depenses:,.0f} FCFA")
    with c3:
        st.metric("📈 Bénéfice net", f"{benefice:,.0f} FCFA")

# ============================================================
# 7. QR CODE
# ============================================================

elif menu == "📲 Générer QR Code":
    st.header("📲 Générateur de QR Code")
    with st.container(border=True):
        type_qr = st.radio("Cible du QR Code :", ["Interface Commande en Ligne", "Interface Commande au Comptoir",
                                                  "Suivi d'une Commande existante"], horizontal=True)
        url_base = st.text_input("URL de base de l'application :",
                                 value="https://cleanup-dyf9esw7bawavodkfst36v.streamlit.app")

        target_url, caption_txt, file_name_out, type_panneau = None, "", "", "comptoir"
        if type_qr == "Interface Commande en Ligne":
            target_url, caption_txt, file_name_out, type_panneau = f"{url_base}/?page=ligne", "Panneau Commande en Ligne", "qr_ligne.png", "ligne"
        elif type_qr == "Interface Commande au Comptoir":
            target_url, caption_txt, file_name_out, type_panneau = f"{url_base}/?page=comptoir", "Panneau Commande Comptoir", "qr_comptoir.png", "comptoir"
        else:
            with get_connection() as conn:
                df_cmds = pd.read_sql_query("SELECT id, nom_client FROM commandes ORDER BY id DESC", conn)
            if not df_cmds.empty:
                cmd_selected = st.selectbox("Choisir une commande :", df_cmds["id"].tolist())
                target_url, caption_txt, file_name_out, type_panneau = f"{url_base}/?cmd_id={cmd_selected}", f"Suivi Commande #{cmd_selected}", f"qr_cmd_{cmd_selected}.png", "ligne"

        if target_url and st.button("Générer le QR Code", type="primary"):
            byte_im = generer_qr_code_bytes(target_url, type_panneau=type_panneau)
            st.image(byte_im, caption=caption_txt, width=320)
            st.download_button("💾 Télécharger l'image PNG", data=byte_im, file_name=file_name_out, mime="image/png")

# ============================================================
# 8. CORBEILLE
# ============================================================

elif menu == "🗑 Corbeille":
    st.header("🗑 Corbeille - Commandes archivées")
    with get_connection() as conn:
        df_corbeille = pd.read_sql_query("SELECT * FROM commandes WHERE dans_corbeille = 1 ORDER BY id DESC", conn)

    if df_corbeille.empty:
        st.info("🗑 La corbeille est vide.")
    else:
        st.dataframe(df_corbeille, use_container_width=True)
