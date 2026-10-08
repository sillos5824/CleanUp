from datetime import datetime, timedelta
import io
import json
import sqlite3

import folium
import pandas as pd
import qrcode
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

# ============================================================
# CONFIGURATION DE LA PAGE & DESIGN CSS
# ============================================================

st.set_page_config(
    page_title="Gestion Pressing",
    page_icon="🧺",
    layout="wide"
)

# Injection de styles CSS personnalisés pour embellir l'interface
st.markdown("""
    <style>
    /* Style général des bannières et conteneurs */
    .stAlert {
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
    }
    /* Style des boutons principaux */
    .stButton>button {
        border-radius: 8px;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    /* Style des blocs de métriques */
    div[data-testid="metric-container"] {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    </style>
""", unsafe_allow_html=True)

DB_NAME = "pressing.db"
NUMERO_WAVE = "01 40 99 46 10"
LIEN_WAVE = f"https://wave.com/send?phone=+225{NUMERO_WAVE.replace(' ', '')}"


# ============================================================
# GESTION SÉCURISÉE DES SECRETS & BASE DE DONNÉES
# ============================================================

def obtenir_base_url() -> str:
    """Récupère l'URL de base exacte depuis st.secrets si disponible, sinon retourne l'URL de production."""
    try:
        return st.secrets.get("BASE_URL", "https://cleanup-mlphsoybeugn4kgrnfcpaz.streamlit.app/")
    except Exception:
        return "https://cleanup-mlphsoybeugn4kgrnfcpaz.streamlit.app/"


def get_connection():
    return sqlite3.connect(DB_NAME)


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
            ("service_choisi", "TEXT DEFAULT 'Lavage + Repassage'")
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
# CONSTANTES & TARIFS
# ============================================================

TARIFS_ARTICLES = {
    # 1. Vêtements Ordinaires
    "Chemise / Polo": 300,
    "T-shirt": 250,
    "Pantalon / Jean": 400,
    "Jupe": 350,
    "Robe simple": 500,

    # 2. Vêtements Spéciaux
    "Robe de cérémonie": 1500,
    "Robe longue / Robe en pagne": 1000,
    "Costume complet (veste + pantalon)": 2000,
    "Veste seule": 1000,
    "Blazer": 1200,
    "Boubou simple": 800,
    "Boubou brodé / Grand boubou": 1500,
    "Tissu pagne (2 m)": 500,
    "Tissu pagne (3 m)": 700,

    # 3. Linge de Maison
    "Drap simple": 700,
    "Drap 2 places": 1000,
    "Drap + taies (set)": 1200,
    "Housse de couette": 1500,
    "Couette légère": 2500,
    "Couette épaisse": 3500,
    "Rideaux légers (la paire)": 1500,
    "Rideaux lourds (la paire)": 2500
}

DESIGNATIONS_DEPENSES = [
    "Salaire du gérant",
    "Achat des intrants (produits lavage)",
    "Facture CIE",
    "Facture SODECI",
    "Autre dépense"
]

SERVICES = ["Lavage + Repassage", "Lavage simple", "Repassage simple"]


# ============================================================
# FONCTIONS UTILES & CALCUL DU DEVIS
# ============================================================

def calculer_devis(articles_selectionnes: dict) -> float:
    """Calcul automatique du prix total selon la grille tarifaire."""
    total = 0.0
    for article, qte in articles_selectionnes.items():
        prix_unitaire = TARIFS_ARTICLES.get(article, 0)
        total += prix_unitaire * qte
    return float(total)


def generer_qr_code_bytes(data_url: str, fill_color: str = "black") -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=3,
    )
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=fill_color, back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def obtenir_nb_nouvelles_commandes():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM commandes WHERE statut_commande = 'En cours' AND dans_corbeille = 0")
        return c.fetchone()[0]


# ============================================================
# EN-TÊTE DÉCORATIF & NAVIGATION LATÉRALE
# ============================================================

# Bannière décorative en haut de page
st.markdown("""
    <div style='background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); padding: 25px; border-radius: 15px; color: white; text-align: center; margin-bottom: 25px;'>
        <h1 style='color: white; margin: 0; font-size: 30px;'>🧺 Pressing Pro - Tableau de Bord</h1>
        <p style='margin: 8px 0 0 0; font-size: 16px; opacity: 0.9;'>Gestion intelligente des commandes, devis et paiements Wave</p>
    </div>
""", unsafe_allow_html=True)

query_params = st.query_params
cmd_id_url = query_params.get("cmd_id", None)
mode_client = query_params.get("mode", None) == "client" or cmd_id_url is not None

st.sidebar.markdown("### 📌 Navigation")
st.sidebar.markdown("---")

if mode_client:
    options_menu = ["📱 Commande en Ligne (Client)"]
    menu = st.sidebar.selectbox("Menu Principal", options_menu)
else:
    nb_nouvelles = obtenir_nb_nouvelles_commandes()
    label_boite = f"📥 Boîte de Réception ({nb_nouvelles})" if nb_nouvelles > 0 else "📥 Boîte de Réception"

    options_menu = [
        label_boite,
        "Nouvelle Commande (Comptoir)",
        "📱 Commande en Ligne (Client)",
        "📲 Générer QR Code",
        "Mise à jour & Retraits",
        "Historique des Commandes",
        "💸 Gestion des Dépenses & Bénéfice",
        "🗑 Corbeille (Archivées > 3 mois)",
    ]

    default_menu_index = 2 if cmd_id_url else 0
    menu = st.sidebar.selectbox("Menu Principal", options_menu, index=default_menu_index)

    if nb_nouvelles > 0:
        st.sidebar.markdown("---")
        st.sidebar.warning(f"🔔 **{nb_nouvelles}** nouvelle(s) commande(s) en attente !")

st.sidebar.markdown("---")
st.sidebar.info("💡 **Astuce :** Utilisez le générateur de QR Code pour faciliter les dépôts clients.")

# ============================================================
# 1. BOÎTE DE RÉCEPTION & NOTIFICATIONS
# ============================================================

if "Boîte de Réception" in menu:
    st.header("📥 Boîte de Réception des Commandes")

    with get_connection() as conn:
        df_recus = pd.read_sql_query(
            "SELECT * FROM commandes WHERE statut_commande = 'En cours' AND dans_corbeille = 0 ORDER BY id DESC",
            conn
        )

    if df_recus.empty:
        st.success("🎉 Aucune nouvelle commande en attente de traitement !")
    else:
        st.info(f"📌 Vous avez **{len(df_recus)}** commande(s) en attente.")

        filtre_type = st.radio("Filtrer par origine :", ["Toutes", "En ligne", "Comptoir"], horizontal=True)

        if filtre_type != "Toutes":
            df_recus = df_recus[df_recus["type_commande"] == filtre_type]

        for _, row in df_recus.iterrows():
            badge_type = "📱 EN LIGNE" if row["type_commande"] == "En ligne" else "🏬 COMPTOIR"

            with st.expander(f"Commande #{row['id']} - {row['nom_client']} ({badge_type}) - Date: {row['date_depot']}"):
                col_a, col_b = st.columns(2)

                with col_a:
                    st.write(f"**Client :** {row['nom_client']}")
                    st.write(f"**Téléphone :** {row['telephone_client']}")
                    st.write(f"**Service :** {row.get('service_choisi', 'Lavage + Repassage')}")
                    if row["type_commande"] == "En ligne":
                        st.write(f"**Adresse :** {row['adresse_livraison'] or 'Non précisée'}")

                with col_b:
                    st.write(f"**Prix total :** {row['prix_total']:,} FCFA")
                    st.write(f"**Modalité :** {row['modalite_paiement']}")
                    st.write(f"**Montant versé :** {row['montant_verse']:,} FCFA")

                st.subheader("🛒 Articles")
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
                        st.toast(f"Commande #{row['id']} passée en traitement !")
                        st.rerun()

                with btn_term:
                    if st.button(f"🏁 Marquer comme Terminée (N°{row['id']})", key=f"fin_{row['id']}"):
                        with get_connection() as conn:
                            c = conn.cursor()
                            c.execute(
                                "UPDATE commandes SET statut_commande = 'Terminées', statut_traitement = 'Terminées' WHERE id = ?",
                                (row['id'],))
                            conn.commit()
                        st.toast(f"Commande #{row['id']} terminée !")
                        st.rerun()


# ============================================================
# 2. NOUVELLE COMMANDE - COMPTOIR
# ============================================================

elif menu == "Nouvelle Commande (Comptoir)":
    st.header("📝 Enregistrer une nouvelle commande (Comptoir)")

    col1, col2 = st.columns(2)

    with col1:
        nom_client = st.text_input("Nom du client *")
        telephone_client = st.text_input("Numéro de téléphone *")
        genre_client = st.selectbox("Genre du client", ["Homme", "Femme", "Autre"])
        date_depot = st.date_input("Date de dépôt", datetime.now())

    with col2:
        service_choisi = st.selectbox("Type de Service *", SERVICES)
        modalite = st.radio("Modalité de paiement *", ["Soldé", "Acompte", "Paiement Wave"])

    st.subheader("🛒 Sélection des articles *")
    articles_selectionnes = {}
    cols = st.columns(3)

    for idx, (item, prix) in enumerate(TARIFS_ARTICLES.items()):
        with cols[idx % 3]:
            qte = st.number_input(f"{item} ({prix} F)", min_value=0, step=1, key=f"depot_{item}")
            if qte > 0:
                articles_selectionnes[item] = qte

    total_articles = sum(articles_selectionnes.values())
    prix_calcule = calculer_devis(articles_selectionnes)

    st.markdown("---")
    st.subheader("💰 Devis Automatique")
    st.info(
        f" Total articles : **{total_articles}** | Service : **{service_choisi}**\n\n"
        f"👉 **Montant total calculé : {prix_calcule:,.0f} FCFA**"
    )

    if modalite == "Paiement Wave":
        st.success(f"📱 **Numéro Wave pour le règlement : {NUMERO_WAVE}**")
        qr_wave = generer_qr_code_bytes(LIEN_WAVE, fill_color="#1DC43C")
        st.image(qr_wave, caption="Scannez pour payer par Wave", width=180)

    montant_verse_input = st.number_input(
        "Montant versé par le client (FCFA)",
        min_value=0.0,
        max_value=float(prix_calcule) if prix_calcule > 0 else 0.0,
        value=float(prix_calcule) if modalite in ["Soldé", "Paiement Wave"] else 0.0,
        step=100.0
    )

    if st.button("Valider la commande", type="primary"):
        montant_verse = prix_calcule if modalite in ["Soldé", "Paiement Wave"] else montant_verse_input

        if not nom_client.strip():
            st.error("❌ Le nom du client est obligatoire.")
        elif not telephone_client.strip():
            st.error("❌ Le numéro de téléphone est obligatoire.")
        elif total_articles == 0:
            st.error("❌ Veuillez sélectionner au moins un article.")
        elif modalite == "Acompte" and montant_verse <= 0:
            st.error("❌ Indiquez un montant d'acompte valide.")
        else:
            articles_recup_initial = {item: 0 for item in articles_selectionnes}

            with get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    INSERT INTO commandes (
                        date_depot, nom_client, telephone_client, genre_client,
                        articles_deposes, articles_recuperes, prix_total, modalite_paiement,
                        montant_verse, statut_traitement, quantite_restante_traitement,
                        date_recuperation, statut_commande, dans_corbeille, latitude,
                        longitude, adresse_livraison, type_commande, service_choisi
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, 'Comptoir', ?)
                """, (
                    str(date_depot), nom_client, telephone_client, genre_client,
                    json.dumps(articles_selectionnes), json.dumps(articles_recup_initial),
                    prix_calcule, modalite, montant_verse, "En cours", total_articles,
                    "-", "En cours", service_choisi
                ))
                new_id = c.lastrowid
                conn.commit()

            st.toast(f"🔔 Commande #{new_id} enregistrée !", icon="📥")
            st.success(f"✅ Commande #{new_id} validée pour un total de {prix_calcule:,.0f} FCFA.")
            st.rerun()


# ============================================================
# 3. COMMANDE EN LIGNE (CLIENT)
# ============================================================

elif menu == "📱 Commande en Ligne (Client)":
    if cmd_id_url:
        st.header(f"🔍 Visualisation de la Commande #{cmd_id_url}")

        with get_connection() as conn:
            df_view = pd.read_sql_query("SELECT * FROM commandes WHERE id = ?", conn, params=(cmd_id_url,))

        if not df_view.empty:
            cmd = df_view.iloc[0]
            st.success(f"✅ Statut actuel de la commande : **{cmd['statut_commande']}**")

            c1, c2 = st.columns(2)
            with c1:
                st.write(f"**Client :** {cmd['nom_client']}")
                st.write(f"**Téléphone :** {cmd['telephone_client']}")
                st.write(f"**Date :** {cmd['date_depot']}")
            with c2:
                service_val = cmd.get('service_choisi', 'Lavage + Repassage') if hasattr(cmd,
                                                                                         'get') else 'Lavage + Repassage'
                st.write(f"**Service :** {service_val}")
                st.write(f"**Total :** {cmd['prix_total']:,} FCFA")
                st.write(f"**Versé :** {cmd['montant_verse']:,} FCFA")

            st.subheader("📦 Articles commandés")
            try:
                articles_dict = json.loads(cmd["articles_deposes"])
                for art, qte in articles_dict.items():
                    st.write(f"- **{art}** : {qte}")
            except Exception:
                st.write(cmd["articles_deposes"])

            st.markdown("---")
            st.subheader("💳 Règlement par Wave")
            st.info(f"Numéro Wave du pressing : **{NUMERO_WAVE}**")
            byte_wave = generer_qr_code_bytes(LIEN_WAVE, fill_color="#1DC43C")
            st.image(byte_wave, caption="Scannez pour effectuer le paiement Wave", width=200)

            if st.button("⬅️ Passer une nouvelle commande"):
                st.query_params.clear()
                st.rerun()
        else:
            st.error("❌ Commande introuvable.")

    else:
        st.header("📱 Passation de commande en ligne")

        if "user_lat" not in st.session_state:
            st.session_state.user_lat = 5.3599517
        if "user_lng" not in st.session_state:
            st.session_state.user_lng = -4.0082563

        st.subheader("📍 1. Localisation")
        col_map1, col_map2 = st.columns([1, 2])

        with col_map1:
            if st.button("🌐 Obtenir ma position GPS"):
                loc = get_geolocation()
                if loc and "coords" in loc:
                    st.session_state.user_lat = loc["coords"]["latitude"]
                    st.session_state.user_lng = loc["coords"]["longitude"]
                    st.success("✅ Position mise à jour !")

            adresse_saisie = st.text_area("Repère / Précision d'adresse")

        with col_map2:
            m = folium.Map(location=[st.session_state.user_lat, st.session_state.user_lng], zoom_start=14)
            folium.Marker([st.session_state.user_lat, st.session_state.user_lng]).add_to(m)
            map_data = st_folium(m, height=250, width="100%", key="map_client")

            if map_data and map_data.get("last_clicked"):
                st.session_state.user_lat = map_data["last_clicked"]["lat"]
                st.session_state.user_lng = map_data["last_clicked"]["lng"]

        st.markdown("---")
        st.subheader("📋 2. Informations & Sélection")

        c1, c2 = st.columns(2)
        with c1:
            nom_client = st.text_input("Nom & Prénom *")
            telephone_client = st.text_input("Téléphone *")
        with c2:
            genre_client = st.selectbox("Genre", ["Homme", "Femme", "Autre"])
            service_choisi = st.selectbox("Prestation souhaitée *", SERVICES)

        st.subheader("🛒 Sélection des articles")
        articles_client = {}
        cols = st.columns(3)

        for idx, (item, prix) in enumerate(TARIFS_ARTICLES.items()):
            with cols[idx % 3]:
                qte = st.number_input(f"{item} ({prix} F)", min_value=0, step=1, key=f"online_{item}")
                if qte > 0:
                    articles_client[item] = qte

        total_art = sum(articles_client.values())
        prix_estime = calculer_devis(articles_client)

        if total_art > 0:
            st.info(f"💡 **Devis estimé : {prix_estime:,.0f} FCFA** ({total_art} article(s) - {service_choisi})")

        st.markdown("---")
        st.subheader("💳 Paiement direct via Wave")
        st.success(f"**Numéro Wave pour vos paiements : {NUMERO_WAVE}**")

        c_qr1, c_qr2 = st.columns([1, 2])
        with c_qr1:
            byte_qr_wave = generer_qr_code_bytes(LIEN_WAVE, fill_color="#1DC43C")
            st.image(byte_qr_wave, caption="Scannez pour régler via l'application Wave", width=180)
        with c_qr2:
            st.markdown(
                f"""
                1. Effectuez votre transfert Wave vers le **{NUMERO_WAVE}**.
                2. Cliquez sur **Envoyer la commande** ci-dessous après validation.
                """
            )
            st.markdown(f"[📱 Ouvrir directement Wave]({LIEN_WAVE})")

        if st.button("Envoyer la commande", type="primary"):
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
                            montant_verse, statut_traitement, quantite_restante_traitement,
                            date_recuperation, statut_commande, dans_corbeille, latitude,
                            longitude, adresse_livraison, type_commande, service_choisi
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'Wave Mobile Money', 0.0, 'En cours', ?, '-', 'En cours', 0, ?, ?, ?, 'En ligne', ?)
                    """, (
                        datetime.now().strftime("%Y-%m-%d"), nom_client, telephone_client,
                        genre_client, json.dumps(articles_client),
                        json.dumps({k: 0 for k in articles_client}), prix_estime,
                        total_art, st.session_state.user_lat, st.session_state.user_lng,
                        adresse_saisie, service_choisi
                    ))
                    last_id = c.lastrowid
                    conn.commit()

                st.success(f"✅ Commande #{last_id} enregistrée pour {prix_estime:,.0f} FCFA !")

                default_base_url = obtenir_base_url()
                byte_qr = generer_qr_code_bytes(f"{default_base_url}?cmd_id={last_id}")

                st.image(byte_qr, caption=f"QR Code Suivi Commande #{last_id}", width=200)


# ============================================================
# 4. GÉNÉRATEUR DE QR CODE MULTI-USAGE
# ============================================================

elif menu == "📲 Générer QR Code":
    st.header("📲 Générateur de QR Code")

    type_qr = st.radio(
        "Objectif :",
        ["Nouveau formulaire de commande", "Commande existante", f"Paiement Wave ({NUMERO_WAVE})"],
        horizontal=True
    )

    default_base_url = obtenir_base_url()
    url_base = st.text_input("Lien Streamlit :", value=default_base_url)

    if type_qr == "Nouveau formulaire de commande":
        target_url = url_base
        caption_txt = "Scannez pour commander en ligne"
        file_name_out = "qr_nouvelle_commande.png"
        fill_color = "black"
    elif type_qr == f"Paiement Wave ({NUMERO_WAVE})":
        target_url = LIEN_WAVE
        caption_txt = f"Paiement Wave - {NUMERO_WAVE}"
        file_name_out = "qr_paiement_wave.png"
        fill_color = "#1DC43C"
    else:
        with get_connection() as conn:
            df_cmds = pd.read_sql_query("SELECT id, nom_client FROM commandes ORDER BY id DESC", conn)

        if df_cmds.empty:
            st.warning("Aucune commande enregistrée.")
            target_url = None
        else:
            cmd_selected = st.selectbox("Commande :", df_cmds["id"].tolist())
            sep = "" if url_base.endswith("/") else "/"
            target_url = f"{url_base}{sep}?cmd_id={cmd_selected}"
            caption_txt = f"Commande #{cmd_selected}"
            file_name_out = f"qr_cmd_{cmd_selected}.png"
            fill_color = "black"

    if target_url and st.button("Générer", type="primary"):
        byte_im = generer_qr_code_bytes(target_url, fill_color=fill_color)
        st.image(byte_im, caption=caption_txt, width=220)
        st.download_button("💾 Télécharger (PNG)", data=byte_im, file_name=file_name_out, mime="image/png")


# ============================================================
# 5. MISE À JOUR & RETRAITS
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
            format_func=lambda x: f"Commande #{x} - {df[df['id'] == x]['nom_client'].values[0]}"
        )
        commande = df[df["id"] == commande_id].iloc[0]

        prix_tot = float(commande['prix_total'] or 0.0)
        montant_v = float(commande['montant_verse'] or 0.0)
        reste_a_payer = max(0.0, prix_tot - montant_v)

        st.markdown("---")
        st.markdown(f"👤 **Client :** {commande['nom_client']}")
        st.markdown(f"📞 **Téléphone :** {commande['telephone_client']}")
        st.markdown(f"📅 **Date dépôt :** {commande['date_depot']}")
        st.markdown(f"💳 **Prix Total :** {prix_tot:,.0f} FCFA")
        st.markdown(f"💵 **Déjà versé :** {montant_v:,.0f} FCFA")
        st.markdown(f"📌 **Reste à payer :** :green[{reste_a_payer:,.0f} FCFA]")
        st.markdown(f"🔄 **Statut Commande :** `:green[{commande['statut_commande']}]`")
        st.markdown(f"🏷️ **Modalité initiale :** {commande['modalite_paiement']}")

        st.markdown("---")
        st.subheader("🧺 1. Vérification des articles retirés")

        try:
            articles_deposes = json.loads(commande["articles_deposes"])
        except Exception:
            articles_deposes = {}

        try:
            articles_recuperes = json.loads(commande["articles_recuperes"])
        except Exception:
            articles_recuperes = {k: 0 for k in articles_deposes}

        nouveaux_recup = {}
        cols_art = st.columns(3)

        for idx, (item, qte_dep) in enumerate(articles_deposes.items()):
            qte_deja_recup = articles_recuperes.get(item, 0)
            with cols_art[idx % 3]:
                st.write(f"**{item}** (Déposés: {qte_dep} | Retirés: {qte_deja_recup})")
                nouveaux_recup[item] = st.number_input(
                    f"Nouveaux retirés ({item})",
                    min_value=qte_deja_recup,
                    max_value=qte_dep,
                    value=qte_deja_recup,
                    key=f"retrait_{commande_id}_{item}"
                )

        st.markdown("---")
        st.subheader("⚙️ 2. Mettre à jour le statut et le paiement")

        col_st1, col_st2 = st.columns(2)
        with col_st1:
            nouveau_statut = st.selectbox(
                "Nouveau statut de la commande",
                ["En cours", "Terminées"],
                index=0 if commande["statut_commande"] == "En cours" else 1
            )
        with col_st2:
            nouveau_montant_verse = st.number_input(
                "Nouveau montant total versé",
                min_value=montant_v,
                value=montant_v,
                step=100.0
            )

        if st.button("💾 Enregistrer les modifications de retrait", type="primary"):
            tot_dep = sum(articles_deposes.values())
            tot_rec = sum(nouveaux_recup.values())
            quantite_restante = max(0, tot_dep - tot_rec)

            date_retrait_str = datetime.now().strftime("%Y-%m-%d %H:%M") if quantite_restante == 0 else commande[
                "date_recuperation"]

            with get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    UPDATE commandes
                    SET articles_recuperes = ?,
                        statut_commande = ?,
                        statut_traitement = ?,
                        montant_verse = ?,
                        quantite_restante_traitement = ?,
                        date_recuperation = ?
                    WHERE id = ?
                """, (
                    json.dumps(nouveaux_recup),
                    nouveau_statut,
                    nouveau_statut,
                    nouveau_montant_verse,
                    quantite_restante,
                    date_retrait_str,
                    commande_id
                ))
                conn.commit()

            st.toast("✅ Retrait et mise à jour enregistrés !", icon="📦")
            st.success("✅ Les données de retrait ont été mises à jour avec succès.")
            st.rerun()


# ============================================================
# 6. HISTORIQUE DES COMMANDES (AVEC RÉINITIALISATION)
# ============================================================

elif menu == "Historique des Commandes":
    st.header("📊 Historique des Commandes Actives")

    col_filtre, col_reset = st.columns([3, 1])
    with col_filtre:
        filtre = st.radio("Filtrer par statut", ["Toutes", "En cours", "Terminées"], horizontal=True)
    with col_reset:
        st.write("")  # Espacement
        if st.button("🔄 Actualiser", use_container_width=True):
            st.rerun()

    with get_connection() as conn:
        if filtre == "Toutes":
            df = pd.read_sql_query("SELECT * FROM commandes WHERE dans_corbeille = 0 ORDER BY id DESC", conn)
        else:
            df = pd.read_sql_query(
                "SELECT * FROM commandes WHERE statut_commande = ? AND dans_corbeille = 0 ORDER BY id DESC", conn,
                params=(filtre,))

    if df.empty:
        st.info("Aucune commande enregistrée.")
    else:
        df_display = df.copy()


        def format_deposes(val):
            try:
                d = json.loads(val)
                return ", ".join([f"{k}: {v}" for k, v in d.items()])
            except Exception:
                return str(val)


        df_display["Articles Déposés"] = df_display["articles_deposes"].apply(format_deposes)

        cols_to_show = [
            "id", "date_depot", "type_commande", "nom_client", "telephone_client",
            "service_choisi", "Articles Déposés", "prix_total", "modalite_paiement",
            "statut_commande"
        ]
        cols_to_show = [col for col in cols_to_show if col in df_display.columns]

        st.dataframe(df_display[cols_to_show], use_container_width=True)

    st.markdown("---")
    st.subheader("⚠️ Réinitialisation globale de l'historique")

    with st.expander("💣 Zone de danger : Supprimer l'historique complet"):
        st.warning(
            "⚠️ Cette action supprimera **définitivement** toutes les commandes de la base de données. Cette opération est irréversible.")

        confirmation = st.checkbox("Je comprends que toutes les commandes seront effacées définitivement.")

        if st.button("🔥 Réinitialiser toutes les commandes", type="primary", disabled=not confirmation):
            with get_connection() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM commandes")
                c.execute("DELETE FROM sqlite_sequence WHERE name='commandes'")
                conn.commit()
            st.toast("✅ Historique entièrement réinitialisé !")
            st.success("Toutes les commandes ont été supprimées.")
            st.rerun()


# ============================================================
# 7. DÉPENSES & BÉNÉFICE
# ============================================================

elif menu == "💸 Gestion des Dépenses & Bénéfice":
    st.header("💸 Gestion des Dépenses & Bénéfice")

    with st.form("form_depense"):
        date_depense = st.date_input("Date", datetime.now())
        designation = st.selectbox("Désignation", DESIGNATIONS_DEPENSES)
        montant_depense = st.number_input("Montant", min_value=0.0, step=100.0)
        commentaire = st.text_area("Commentaire")
        ajouter_depense = st.form_submit_button("Ajouter")

    if ajouter_depense and montant_depense > 0:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO depenses (date_depense, designation, montant, commentaire) VALUES (?, ?, ?, ?)",
                (str(date_depense), designation, montant_depense, commentaire)
            )
            conn.commit()
        st.success("Dépense enregistrée.")
        st.rerun()

    with get_connection() as conn:
        total_commandes = pd.read_sql_query(
            "SELECT COALESCE(SUM(prix_total), 0) AS total FROM commandes WHERE dans_corbeille = 0", conn
        ).iloc[0]["total"]
        total_depenses = pd.read_sql_query(
            "SELECT COALESCE(SUM(montant), 0) AS total FROM depenses", conn
        ).iloc[0]["total"]

    benefice = total_commandes - total_depenses

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💰 Chiffre d'affaires", f"{total_commandes:,.0f} FCFA")
    with col2:
        st.metric("💸 Total dépenses", f"{total_depenses:,.0f} FCFA")
    with col3:
        st.metric("📈 Bénéfice", f"{benefice:,.0f} FCFA")


# ============================================================
# 8. CORBEILLE
# ============================================================

elif menu == "🗑 Corbeille (Archivées > 3 mois)":
    st.header("🗑 Corbeille - Commandes archivées")
    limite_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    with get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE commandes SET dans_corbeille = 1 WHERE date_depot < ? AND statut_commande = 'Terminées'",
            (limite_date,)
        )
        conn.commit()

        df_corbeille = pd.read_sql_query(
            "SELECT * FROM commandes WHERE dans_corbeille = 1 ORDER BY id DESC", conn
        )

    if df_corbeille.empty:
        st.info("🗑 La corbeille est vide.")
    else:
        st.dataframe(df_corbeille, use_container_width=True)
