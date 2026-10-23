from datetime import datetime, timedelta
import io
import json
import sqlite3

import folium
import pandas as pd
import qrcode
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from streamlit_searchbox import st_searchbox

# ============================================================
# CONFIGURATION DE LA PAGE & DESIGN CSS GLOBAL
# ============================================================

st.set_page_config(
    page_title="CleanUp+ | Gestion Pressing",
    page_icon="🧺",
    layout="wide"
)

# Injection de styles CSS pour un design moderne, épuré et "beau à voir"
st.markdown("""
    <style>
        /* Import d'une police moderne */
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Plus Jakarta+ Sans', sans-serif;
        }

        /* Style global du fond principal */
        .stApp {
            background-color: #f8fafc;
        }

        /* Personnalisation des boutons principaux */
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

        /* Style des champs de saisie */
        .stTextInput>div>div>input, .stSelectbox>div>div>select, .stTextArea>div>div>textarea {
            border-radius: 8px;
            border: 1px solid #cbd5e1;
            background-color: #ffffff;
        }

        /* Cartes et conteneurs visuels */
        .css-1r6slb0, div[data-testid="stExpander"] {
            border-radius: 10px;
            border: 1px solid #e2e8f0;
            background-color: #ffffff;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
        }
    </style>
""", unsafe_allow_html=True)

DB_NAME = "pressing.db"
NUMERO_WAVE = "0140994610"


# ============================================================
# GESTION DE LA BASE DE DONNÉES
# ============================================================

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
    # 1. Vêtements Ordinaires
    "Chemise / Polo": 300.0,
    "T-shirt": 250.0,
    "Pantalon / Jean": 400.0,
    "Jupe": 350.0,
    "Robe simple": 500.0,
    # 2. Vêtements Spéciaux
    "Robe de cérémonie": 1500.0,
    "Robe longue / Robe en pagne": 1000.0,
    "Costume complet (veste + pantalon)": 2000.0,
    "Veste seule": 1000.0,
    "Blazer": 1200.0,
    "Boubou simple": 800.0,
    "Boubou brodé / Grand boubou": 1500.0,
    "Tissu pagne (2 m)": 500.0,
    "Tissu pagne (3 m)": 700.0,
    # 3. Linge de Maison
    "Drap simple": 700.0,
    "Drap 2 places": 1000.0,
    "Drap + taies (set)": 1200.0,
    "Housse de couette": 1500.0,
    "Couette légère": 2500.0,
    "Couette épaisse": 3500.0,
    "Rideaux légers (la paire)": 1500.0,
    "Rideaux lourds (la paire)": 2500.0,
}

vetements_ordinaires = ["Chemise / Polo", "T-shirt", "Pantalon / Jean", "Jupe", "Robe simple"]
vetements_speciaux = [
    "Robe de cérémonie", "Robe longue / Robe en pagne",
    "Costume complet (veste + pantalon)", "Veste seule", "Blazer",
    "Boubou simple", "Boubou brodé / Grand boubou",
    "Tissu pagne (2 m)", "Tissu pagne (3 m)"
]
linge_maison = [
    "Drap simple", "Drap 2 places", "Drap + taies (set)",
    "Housse de couette", "Couette légère", "Couette épaisse",
    "Rideaux légers (la paire)", "Rideaux lourds (la paire)"
]


def calculer_devis(articles_selectionnes: dict, service: str) -> float:
    total = 0.0
    for article, qte in articles_selectionnes.items():
        if qte > 0 and article in TARIFS_BASE:
            prix_base = TARIFS_BASE[article]
            total += prix_base * qte * (1.0 if service == "Lavage + Repassage" else 0.75)
    return float(total)


def generer_qr_code_bytes(data_url: str) -> bytes:
    img = qrcode.make(data_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def obtenir_nb_nouvelles_commandes():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM commandes WHERE statut_commande = 'En cours' AND dans_corbeille = 0")
        return c.fetchone()[0]


# Fonction de recherche instantanée élargie à toutes les communes, quartiers et rues d'Abidjan
def search_adresse_autocomplete(searchterm: str):
    if not searchterm or len(searchterm.strip()) < 2:
        return []
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": f"{searchterm}, Abidjan, Côte d'Ivoire",
        "format": "json",
        "limit": 8,
        "addressdetails": 1,
        "countrycodes": "ci"
    }
    headers = {"User-Agent": "PressingAppStreamlit/1.0"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=4)
        if response.status_code == 200:
            data = response.json()
            return [(item["display_name"], item) for item in data]
    except Exception:
        pass
    return []


# ============================================================
# CONSTANTES
# ============================================================

DESIGNATIONS_DEPENSES = [
    "Salaire du gérant",
    "Achat des intrants (produits lavage)",
    "Facture CIE",
    "Facture SODECI",
    "Autre dépense"
]

SERVICES = ["Lavage + Repassage", "Lavage simple", "Repassage simple"]
MODALITES_PAIEMENT = ["Soldé", "Acompte"]
MODES_PAIEMENT = ["Espèces", "Wave"]

# ============================================================
# NAVIGATION LATÉRALE ET GESTION DES PARAMÈTRES URL
# ============================================================

query_params = st.query_params
cmd_id_url = query_params.get("cmd_id", None)
page_url = query_params.get("page", None)

# RESTRICTION DE LA NAVIGATION SI SCANNÉ EN LIGNE
if cmd_id_url or page_url == "ligne":
    liste_menus = ["📱 Commande en Ligne (Client)"]
    menu = st.sidebar.selectbox("Navigation Client", liste_menus, index=0)
    st.sidebar.info("📱 Espace Client Dédié — CleanUp+")
else:
    # ============================================================
    # TABLEAU DE BORD - GÉRANT (DESIGN ÉPURÉ & MODERNE)
    # ============================================================
    st.markdown("""
        <div style="
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            padding: 35px;
            border-radius: 16px;
            color: white;
            text-align: center;
            box-shadow: 0 10px 25px -5px rgba(30, 60, 114, 0.3);
            margin-bottom: 30px;
        ">
            <h1 style="color: white; margin: 0; font-size: 2.8em; font-weight: 700; letter-spacing: -0.5px;">🧺 CleanUp+</h1>
            <p style="font-size: 1.2em; margin-top: 12px; opacity: 0.95; font-weight: 400;">Votre espace de gestion de pressing intelligent et connecté</p>
        </div>
    """, unsafe_allow_html=True)

    col_m1, col_m2, col_m3 = st.columns(3)

    with col_m1:
        st.markdown("""
            <div style="background-color: #ffffff; padding: 22px; border-radius: 12px; border-left: 5px solid #1e3c72; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); text-align: center;">
                <h4 style="color: #1e3c72; margin: 0; font-size: 1.1em; font-weight: 600;">Commandes en cours</h4>
                <p style="font-size: 1.7em; font-weight: 700; color: #0f172a; margin: 8px 0 0 0;">📦 Actives</p>
            </div>
        """, unsafe_allow_html=True)

    with col_m2:
        st.markdown("""
            <div style="background-color: #ffffff; padding: 22px; border-radius: 12px; border-left: 5px solid #2a5298; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); text-align: center;">
                <h4 style="color: #2a5298; margin: 0; font-size: 1.1em; font-weight: 600;">Service Populaire</h4>
                <p style="font-size: 1.4em; font-weight: 700; color: #0f172a; margin: 8px 0 0 0;">Lavage + Repassage</p>
            </div>
        """, unsafe_allow_html=True)

    with col_m3:
        st.markdown("""
            <div style="background-color: #ffffff; padding: 22px; border-radius: 12px; border-left: 5px solid #1565c0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); text-align: center;">
                <h4 style="color: #1565c0; margin: 0; font-size: 1.1em; font-weight: 600;">Paiement Rapide</h4>
                <p style="font-size: 1.4em; font-weight: 700; color: #0f172a; margin: 8px 0 0 0;">📱 Wave & Espèces</p>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    nb_nouvelles = obtenir_nb_nouvelles_commandes()
    label_boite = f"📥 Boîte de Réception ({nb_nouvelles})" if nb_nouvelles > 0 else "📥 Boîte de Réception"

    # ============================================================
    # NAVIGATION PAR RUBRIQUES (ADMINISTRATION CLEANUP+)
    # ============================================================
    st.sidebar.markdown("### 🛠️ Administration CleanUp+")

    rubriques_admin = {
        "📦 Exploitation & Suivi": [
            label_boite,
            "Nouvelle Commande (Comptoir)",
            "📱 Commande en Ligne (Client)",
            "Mise à jour & Retraits",
            "Historique des Commandes"
        ],
        "💰 Finances & Comptabilité": [
            "💸 Gestion des Dépenses & Bénéfice"
        ],
        "⚙️ Outils & Système": [
            "📲 Générer QR Code",
            "🗑 Corbeille (Archivées > 3 mois)"
        ]
    }

    rubrique_choisie = st.sidebar.selectbox("📂 Choisir une rubrique", list(rubriques_admin.keys()))
    menu = st.sidebar.selectbox("🎯 Action", rubriques_admin[rubrique_choisie])

    if nb_nouvelles > 0:
        st.sidebar.warning(f"🔔 **{nb_nouvelles}** nouvelle(s) commande(s) en attente !")

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
                    st.write(f"**Mode de paiement :** {row.get('mode_paiement', 'Espèces')}")
                    if row.get('mode_paiement') == 'Wave':
                        st.info(f"📱 Numéro Wave : **{NUMERO_WAVE}**")
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
        modalite = st.radio("Modalité de paiement *", MODALITES_PAIEMENT)
        mode_paiement = st.selectbox("Mode de paiement *", MODES_PAIEMENT)

    if mode_paiement == "Wave":
        st.info(f"📱 Veuillez effectuer le transfert Wave sur le numéro : **{NUMERO_WAVE}**")

    st.subheader("🛒 Sélection des articles par catégorie (Grille Tarifaire)")

    articles_selectionnes = {}

    st.markdown("### 1. Vêtements Ordinaires")
    cols_v1 = st.columns(3)
    for idx, item in enumerate(vetements_ordinaires):
        with cols_v1[idx % 3]:
            prix_unit = TARIFS_BASE[item]
            qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"depot_{item}")
            if qte > 0:
                articles_selectionnes[item] = qte

    st.markdown("### 2. Vêtements Spéciaux")
    cols_v2 = st.columns(3)
    for idx, item in enumerate(vetements_speciaux):
        with cols_v2[idx % 3]:
            prix_unit = TARIFS_BASE[item]
            qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"depot_{item}")
            if qte > 0:
                articles_selectionnes[item] = qte

    st.markdown("### 3. Linge de Maison")
    cols_v3 = st.columns(3)
    for idx, item in enumerate(linge_maison):
        with cols_v3[idx % 3]:
            prix_unit = TARIFS_BASE[item]
            qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"depot_{item}")
            if qte > 0:
                articles_selectionnes[item] = qte

    total_articles = sum(articles_selectionnes.values())
    prix_calcule = calculer_devis(articles_selectionnes, service_choisi)

    st.markdown("---")
    st.subheader("💰 Devis & Paiement")
    st.info(
        f" Total articles : **{total_articles}** | Service : **{service_choisi}**\n\n"
        f"👉 **Montant total calculé : {prix_calcule:,.0f} FCFA**"
    )

    if modalite == "Acompte":
        montant_verse_input = st.number_input(
            "Préciser le montant versé (FCFA) *",
            min_value=0.0,
            max_value=float(prix_calcule) if prix_calcule > 0 else 0.0,
            value=0.0,
            step=100.0
        )
    else:
        montant_verse_input = prix_calcule

    if st.button("Valider la commande", type="primary"):
        montant_verse = prix_calcule if modalite == "Soldé" else montant_verse_input

        if not nom_client.strip():
            st.error("❌ Le nom du client est obligatoire.")
        elif not telephone_client.strip():
            st.error("❌ Le numéro de téléphone est obligatoire.")
        elif total_articles == 0:
            st.error("❌ Veuillez sélectionner au moins un article.")
        elif modalite == "Acompte" and montant_verse <= 0:
            st.error("❌ Veuillez préciser un montant versé valide pour l'acompte.")
        elif modalite == "Acompte" and montant_verse >= prix_calcule:
            st.error("❌ Le montant de l'acompte ne peut pas être supérieur ou égal au prix total (choisissez 'Soldé').")
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
                st.write(f"**Service :** {cmd.get('service_choisi', 'Lavage + Repassage')}")
                st.write(f"**Total :** {cmd['prix_total']:,} FCFA")
                st.write(f"**Modalité :** {cmd['modalite_paiement']}")
                st.write(f"**Versé :** {cmd['montant_verse']:,} FCFA")
                st.write(f"**Mode de paiement :** {cmd.get('mode_paiement', 'Espèces')}")
                if cmd.get('mode_paiement') == 'Wave':
                    st.info(f"📱 Numéro Wave pour le paiement : **{NUMERO_WAVE}**")

            st.subheader("📦 Articles commandés")
            try:
                articles_dict = json.loads(cmd["articles_deposes"])
                for art, qte in articles_dict.items():
                    st.write(f"- **{art}** : {qte}")
            except Exception:
                st.write(cmd["articles_deposes"])

            if not (cmd_id_url or page_url == "ligne"):
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

        st.subheader("📍 1. Localisation (Recherche instantanée : Communes, Quartiers, Rues d'Abidjan)")

        col_map1, col_map2 = st.columns([1, 2])

        with col_map1:
            if st.button("🌐 Obtenir ma position GPS automatique"):
                loc = get_geolocation()
                if loc and "coords" in loc:
                    st.session_state.user_lat = loc["coords"]["latitude"]
                    st.session_state.user_lng = loc["coords"]["longitude"]
                    st.success("✅ Position GPS actualisée !")

            st.markdown("---")

            # Recherche d'adresse instantanée ultra-large pour Abidjan
            selected_adresse_data = st_searchbox(
                search_adresse_autocomplete,
                key="adresse_searchbox",
                placeholder="Ex: Cocody Angré, Marcory Zone 4, Boulevard Lagunaire...",
                label="Recherche par Commune, Quartier ou Rue"
            )

            adresse_finale = ""
            if selected_adresse_data:
                adresse_finale = selected_adresse_data["display_name"]
                st.session_state.user_lat = float(selected_adresse_data["lat"])
                st.session_state.user_lng = float(selected_adresse_data["lon"])
                st.success("📍 Adresse localisée avec succès !")
            else:
                adresse_finale = st.text_input("Précisions de l'adresse (Bâtiment, porte...)")

        with col_map2:
            m = folium.Map(location=[st.session_state.user_lat, st.session_state.user_lng], zoom_start=14)
            folium.Marker([st.session_state.user_lat, st.session_state.user_lng], popup="Lieu de livraison").add_to(m)
            map_data = st_folium(m, height=270, width="100%", key="map_client")

            if map_data and map_data.get("last_clicked"):
                st.session_state.user_lat = map_data["last_clicked"]["lat"]
                st.session_state.user_lng = map_data["last_clicked"]["lng"]

        st.markdown("---")
        st.subheader("📋 2. Informations, Prestation & Paiement")

        c1, c2 = st.columns(2)
        with c1:
            nom_client = st.text_input("Nom & Prénom *")
            telephone_client = st.text_input("Téléphone *")
            genre_client = st.selectbox("Genre", ["Homme", "Femme", "Autre"])
        with c2:
            service_choisi = st.selectbox("Prestation souhaitée *", SERVICES)
            modalite_client = st.radio("Modalité *", MODALITES_PAIEMENT)
            mode_paiement_client = st.selectbox("Mode de paiement *", MODES_PAIEMENT)

        if mode_paiement_client == "Wave":
            st.warning(f"📱 Veuillez effectuer votre paiement par Wave au numéro : **{NUMERO_WAVE}**")

        st.subheader("🛒 Sélection des articles (Grille Tarifaire)")
        articles_client = {}

        st.markdown("### 1. Vêtements Ordinaires")
        cols_c1 = st.columns(3)
        for idx, item in enumerate(vetements_ordinaires):
            with cols_c1[idx % 3]:
                prix_unit = TARIFS_BASE[item]
                qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"online_{item}")
                if qte > 0:
                    articles_client[item] = qte

        st.markdown("### 2. Vêtements Spéciaux")
        cols_c2 = st.columns(3)
        for idx, item in enumerate(vetements_speciaux):
            with cols_c2[idx % 3]:
                prix_unit = TARIFS_BASE[item]
                qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"online_{item}")
                if qte > 0:
                    articles_client[item] = qte

        st.markdown("### 3. Linge de Maison")
        cols_c3 = st.columns(3)
        for idx, item in enumerate(linge_maison):
            with cols_c3[idx % 3]:
                prix_unit = TARIFS_BASE[item]
                qte = st.number_input(f"{item} ({prix_unit:,.0f} F)", min_value=0, step=1, key=f"online_{item}")
                if qte > 0:
                    articles_client[item] = qte

        total_art = sum(articles_client.values())
        prix_estime = calculer_devis(articles_client, service_choisi)

        if total_art > 0:
            st.info(f"💡 **Devis estimé : {prix_estime:,.0f} FCFA** ({total_art} article(s) - {service_choisi})")

        if modalite_client == "Acompte":
            montant_verse_client = st.number_input(
                "Préciser le montant versé (FCFA) *",
                min_value=0.0,
                max_value=float(prix_estime) if prix_estime > 0 else 0.0,
                value=0.0,
                step=100.0,
                key="acompte_client_en_ligne"
            )
        else:
            montant_verse_client = prix_estime

        if st.button("Envoyer la commande", type="primary"):
            if not nom_client.strip():
                st.error("❌ Le nom est obligatoire.")
            elif not telephone_client.strip():
                st.error("❌ Le téléphone est obligatoire.")
            elif total_art == 0:
                st.error("❌ Sélectionnez au moins un article.")
            elif modalite_client == "Acompte" and montant_verse_client <= 0:
                st.error("❌ Veuillez préciser un montant versé valide pour l'acompte.")
            elif modalite_client == "Acompte" and montant_verse_client >= prix_estime:
                st.error(
                    "❌ Le montant de l'acompte ne peut pas être supérieur ou égal au prix total (choisissez 'Soldé').")
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
                        datetime.now().strftime("%Y-%m-%d"), nom_client, telephone_client,
                        genre_client, json.dumps(articles_client),
                        json.dumps({k: 0 for k in articles_client}), prix_estime,
                        modalite_client, mode_paiement_client, montant_verse_client,
                        total_art, st.session_state.user_lat, st.session_state.user_lng,
                        adresse_finale, service_choisi
                    ))
                    last_id = c.lastrowid
                    conn.commit()

                st.success(f"✅ Commande #{last_id} enregistrée avec succès pour {prix_estime:,.0f} FCFA !")

                base_url = "https://cleanup-dyf9esw7bawavodkfst36v.streamlit.app"
                byte_qr = generer_qr_code_bytes(f"{base_url}/?cmd_id={last_id}")

                st.image(byte_qr, caption=f"QR Code Commande #{last_id}", width=200)


# ============================================================
# 4. GÉNÉRATEUR DE QR CODE MULTI-USAGE
# ============================================================

elif menu == "📲 Générer QR Code":
    st.header("📲 Générateur de QR Code")

    type_qr = st.radio(
        "Objectif du QR Code :",
        ["Interface Commande en Ligne", "Interface Commande au Comptoir", "Suivi d'une Commande existante"],
        horizontal=True
    )

    url_base = st.text_input("Lien de base de votre application Streamlit :",
                             value="https://cleanup-dyf9esw7bawavodkfst36v.streamlit.app")

    target_url = None
    caption_txt = ""
    file_name_out = ""

    if type_qr == "Interface Commande en Ligne":
        target_url = f"{url_base}/?page=ligne"
        caption_txt = "Scannez pour accéder aux commandes en ligne"
        file_name_out = "qr_commande_en_ligne.png"

    elif type_qr == "Interface Commande au Comptoir":
        target_url = f"{url_base}/?page=comptoir"
        caption_txt = "Scannez pour accéder aux commandes au comptoir"
        file_name_out = "qr_commande_comptoir.png"

    else:
        with get_connection() as conn:
            df_cmds = pd.read_sql_query("SELECT id, nom_client FROM commandes ORDER BY id DESC", conn)

        if df_cmds.empty:
            st.warning("Aucune commande enregistrée.")
        else:
            cmd_selected = st.selectbox("Sélectionner une commande :", df_cmds["id"].tolist())
            target_url = f"{url_base}/?cmd_id={cmd_selected}"
            caption_txt = f"Suivi Commande #{cmd_selected}"
            file_name_out = f"qr_cmd_{cmd_selected}.png"

    if target_url and st.button("Générer le QR Code", type="primary"):
        byte_im = generer_qr_code_bytes(target_url)
        st.image(byte_im, caption=caption_txt, width=220)
        st.download_button("💾 Télécharger le QR Code (PNG)", data=byte_im, file_name=file_name_out, mime="image/png")


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
            format_func=lambda
                x: f"Commande #{x} - {df[df['id'] == x]['nom_client'].values[0]} ({df[df['id'] == x]['type_commande'].values[0]})"
        )
        commande = df[df["id"] == commande_id].iloc[0]

        prix_tot = float(commande['prix_total'] or 0.0)
        montant_v = float(commande['montant_verse'] or 0.0)
        reste_a_payer = max(0.0, prix_tot - montant_v)

        st.markdown("---")
        col_info1, col_info2 = st.columns(2)

        with col_info1:
            st.markdown(f"👤 **Client :** {commande['nom_client']}")
            st.markdown(f"📞 **Téléphone :** {commande['telephone_client']}")
            st.markdown(f"📅 **Date dépôt :** {commande['date_depot']}")
            st.markdown(f"📱 **Origine :** {commande['type_commande']}")
            if commande['type_commande'] == 'En ligne' and commande['adresse_livraison']:
                st.markdown(f"📍 **Adresse / Repère :** {commande['adresse_livraison']}")

        with col_info2:
            st.markdown(f"💳 **Prix Total :** {prix_tot:,.0f} FCFA")
            st.markdown(f"💵 **Déjà versé :** {montant_v:,.0f} FCFA")
            st.markdown(f"📌 **Reste à payer :** :green[{reste_a_payer:,.0f} FCFA]")
            st.markdown(f"🔄 **Statut Commande :** `:green[{commande['statut_commande']}]`")
            st.markdown(f"🏷️ **Modalité :** {commande['modalite_paiement']}")
            current_mode = commande.get('mode_paiement', 'Espèces')
            st.markdown(f"💳 **Mode de paiement :** {current_mode}")
            if current_mode == 'Wave':
                st.info(f"📱 Numéro Wave : **{NUMERO_WAVE}**")

        if commande['type_commande'] == 'En ligne' and pd.notna(commande.get('latitude')) and pd.notna(
                commande.get('longitude')):
            st.markdown("---")
            st.subheader("🗺️ Position géographique du client (Commande en ligne)")

            lat_client = float(commande['latitude'])
            lon_client = float(commande['longitude'])

            google_maps_url = f"https://www.google.com/maps/search/?api=1&query={lat_client},{lon_client}"
            st.markdown(
                f"""<a href="{google_maps_url}" target="_blank" style="
                    display: inline-block;
                    background-color: #2a5298;
                    color: white;
                    padding: 10px 20px;
                    border-radius: 5px;
                    text-decoration: none;
                    font-weight: bold;
                    margin-bottom: 15px;
                ">🗺️ Ouvrir l'itinéraire du client dans Google Maps</a>""",
                unsafe_allow_html=True
            )

            m_client = folium.Map(location=[lat_client, lon_client], zoom_start=15)
            folium.Marker(
                [lat_client, lon_client],
                popup=f"Client: {commande['nom_client']}",
                icon=folium.Icon(color="blue", icon="user")
            ).add_to(m_client)
            st_folium(m_client, height=300, width="100%", key=f"map_retrait_{commande_id}")

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

        col_st1, col_st2, col_st3, col_st4 = st.columns(4)
        with col_st1:
            nouveau_statut = st.selectbox(
                "Nouveau statut de la commande",
                ["En cours", "Terminées"],
                index=0 if commande["statut_commande"] == "En cours" else 1
            )
        with col_st2:
            nouvelle_modalite = st.selectbox(
                "Modalité de paiement",
                MODALITES_PAIEMENT,
                index=0 if commande["modalite_paiement"] not in MODALITES_PAIEMENT else MODALITES_PAIEMENT.index(
                    commande["modalite_paiement"])
            )
        with col_st3:
            nouveau_mode = st.selectbox(
                "Mode de paiement",
                MODES_PAIEMENT,
                index=0 if current_mode not in MODES_PAIEMENT else MODES_PAIEMENT.index(current_mode)
            )
        with col_st4:
            if nouvelle_modalite == "Acompte":
                nouveau_montant_verse = st.number_input(
                    "Montant versé (Acompte)",
                    min_value=0.0,
                    max_value=prix_tot,
                    value=montant_v,
                    step=100.0
                )
            else:
                nouveau_montant_verse = prix_tot

        if nouveau_mode == "Wave":
            st.info(f"📱 Rappel du numéro Wave : **{NUMERO_WAVE}**")

        if st.button("💾 Enregistrer les modifications de retrait", type="primary"):
            if nouvelle_modalite == "Soldé":
                nouveau_montant_verse = prix_tot

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
                        modalite_paiement = ?,
                        mode_paiement = ?,
                        montant_verse = ?,
                        quantite_restante_traitement = ?,
                        date_recuperation = ?
                    WHERE id = ?
                """, (
                    json.dumps(nouveaux_recup),
                    nouveau_statut,
                    nouveau_statut,
                    nouvelle_modalite,
                    nouveau_mode,
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
# 6. HISTORIQUE DES COMMANDES
# ============================================================

elif menu == "Historique des Commandes":
    col_h1, col_h2, col_h3 = st.columns([3, 1, 1])
    with col_h1:
        st.header("📊 Historique des Commandes Actives")
    with col_h2:
        if st.button("🔄 Actualiser", use_container_width=True):
            st.rerun()
    with col_h3:
        btn_raz = st.button("🗑️ Vider l'historique", type="secondary", use_container_width=True)

    # Zone de confirmation pour la remise à zéro de l'historique
    if btn_raz:
        st.session_state.confirmation_raz = True

    if st.session_state.get("confirmation_raz", False):
        st.warning("⚠️ Attention : Voulez-vous vraiment archiver toutes les commandes actives vers la corbeille ?")
        col_conf1, col_conf2 = st.columns(2)
        with col_conf1:
            if st.button("✅ Oui, tout archiver", type="primary"):
                with get_connection() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE commandes SET dans_corbeille = 1 WHERE dans_corbeille = 0")
                    conn.commit()
                st.session_state.confirmation_raz = False
                st.success("✅ L'historique a été vidé avec succès !")
                st.rerun()
        with col_conf2:
            if st.button("❌ Annuler"):
                st.session_state.confirmation_raz = False
                st.rerun()

    filtre = st.radio("Filtrer par statut", ["Toutes", "En cours", "Terminées"], horizontal=True)

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
            "mode_paiement", "statut_commande"
        ]
        cols_to_show = [col for col in cols_to_show if col in df_display.columns]

        st.dataframe(df_display[cols_to_show], use_container_width=True)


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
        df_corbeille_display = df_corbeille.copy()
        def format_deposes_corbeille(val):
            try:
                d = json.loads(val)
                return ", ".join([f"{k}: {v}" for k, v in d.items()])
            except Exception:
                return str(val)
        if "articles_deposes" in df_corbeille_display.columns:
            df_corbeille_display["articles_deposes"] = df_corbeille_display["articles_deposes"].apply(format_deposes_corbeille)
        st.dataframe(df_corbeille_display, use_container_width=True)
