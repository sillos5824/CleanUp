import json
import sqlite3
from datetime import datetime, timedelta
import io

import pandas as pd
import streamlit as st

# Imports pour le QR code et la carte
import qrcode
import folium
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation


# ============================================================
# CONFIGURATION DE LA PAGE
# ============================================================

st.set_page_config(
    page_title="Gestion Pressing",
    page_icon="🧺",
    layout="wide"
)


# ============================================================
# INITIALISATION DE LA BASE DE DONNÉES
# ============================================================

def init_db():
    conn = sqlite3.connect("pressing.db")
    c = conn.cursor()

    # Création de la table commandes
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
            adresse_livraison TEXT
        )
    """)

    # Vérification des colonnes existantes
    c.execute("PRAGMA table_info(commandes)")
    columns = [column[1] for column in c.fetchall()]

    # Migration pour les anciennes bases
    if "telephone_client" not in columns:
        try:
            c.execute("ALTER TABLE commandes ADD COLUMN telephone_client TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    if "latitude" not in columns:
        try:
            c.execute("ALTER TABLE commandes ADD COLUMN latitude REAL")
        except sqlite3.OperationalError:
            pass

    if "longitude" not in columns:
        try:
            c.execute("ALTER TABLE commandes ADD COLUMN longitude REAL")
        except sqlite3.OperationalError:
            pass

    if "adresse_livraison" not in columns:
        try:
            c.execute("ALTER TABLE commandes ADD COLUMN adresse_livraison TEXT")
        except sqlite3.OperationalError:
            pass

    # Création de la table dépenses
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
    conn.close()


init_db()


def get_connection():
    return sqlite3.connect("pressing.db")


# ============================================================
# LISTES
# ============================================================

TYPES_ARTICLES = [
    "Chemise",
    "Pantalon",
    "Veste / Costume",
    "Robe",
    "Manteau / Blouson",
    "Chaussures (Baskets/Cuir)",
    "Linge de lit / Couette",
    "T-shirt / Polo",
    "Autre"
]

DESIGNATIONS_DEPENSES = [
    "Salaire du gérant",
    "Achat des intrants (produits lavage)",
    "Facture CIE",
    "Facture SODECI",
    "Autre dépense"
]


# ============================================================
# NAVIGATION
# ============================================================

st.title("🧺 Application de Suivi de Pressing")

menu = st.sidebar.selectbox(
    "Navigation",
    [
        "Nouvelle Commande",
        "📱 Commande en Ligne (Client)",
        "📲 Générer QR Code",
        "Mise à jour & Retraits",
        "Historique des Commandes",
        "💸 Gestion des Dépenses & Bénéfice",
        "🗑 Corbeille (Archivées > 3 mois)",
    ],
)


# ============================================================
# 1. NOUVELLE COMMANDE - COMPTOIR
# ============================================================

if menu == "Nouvelle Commande":

    st.header("📝 Enregistrer une nouvelle commande (Comptoir)")

    with st.form("form_nouvelle_commande", clear_on_submit=True):

        col1, col2 = st.columns(2)

        with col1:
            nom_client = st.text_input("Nom du client *")
            telephone_client = st.text_input("Numéro de téléphone *")
            genre_client = st.selectbox("Genre du client", ["Homme", "Femme", "Autre"])
            date_depot = st.date_input("Date de dépôt", datetime.now())

        with col2:
            prix_total = st.number_input("Prix total (€ ou FCFA) *", min_value=0.0, step=100.0)
            modalite = st.radio("Modalité de paiement *", ["Soldé", "Acompte"])
            montant_verse_input = st.number_input("Montant versé (Acompte si applicable)", min_value=0.0, step=100.0)

        st.subheader("🛒 Sélection des articles *")

        articles_selectionnes = {}
        cols = st.columns(3)

        for idx, item in enumerate(TYPES_ARTICLES):
            with cols[idx % 3]:
                qte = st.number_input(f"{item}", min_value=0, step=1, key=f"depot_{item}")
                if qte > 0:
                    articles_selectionnes[item] = qte

        submitted = st.form_submit_button("Valider la commande", type="primary")

    if submitted:
        total_articles = sum(articles_selectionnes.values())
        montant_verse = prix_total if modalite == "Soldé" else montant_verse_input

        if not nom_client.strip():
            st.error("❌ Le champ 'Nom du client' est obligatoire.")
        elif not telephone_client.strip():
            st.error("❌ Le champ 'Numéro de téléphone' est obligatoire.")
        elif prix_total <= 0:
            st.error("❌ Le 'Prix total' doit être supérieur à 0.")
        elif modalite == "Acompte" and montant_verse <= 0:
            st.error("❌ Veuillez indiquer un montant d'acompte supérieur à 0.")
        elif total_articles == 0:
            st.error("❌ Veuillez sélectionner au moins un article.")
        else:
            articles_recup_initial = {item: 0 for item in articles_selectionnes}

            conn = get_connection()
            c = conn.cursor()

            c.execute("""
                INSERT INTO commandes (
                    date_depot, nom_client, telephone_client, genre_client,
                    articles_deposes, articles_recuperes, prix_total, modalite_paiement,
                    montant_verse, statut_traitement, quantite_restante_traitement,
                    date_recuperation, statut_commande, dans_corbeille, latitude,
                    longitude, adresse_livraison
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL
                )
            """, (
                str(date_depot),
                nom_client,
                telephone_client,
                genre_client,
                json.dumps(articles_selectionnes),
                json.dumps(articles_recup_initial),
                prix_total,
                modalite,
                montant_verse,
                "En cours",
                total_articles,
                "-",
                "En cours"
            ))

            conn.commit()
            conn.close()

            st.success("✅ Commande enregistrée avec succès !")
            st.rerun()


# ============================================================
# 2. COMMANDE EN LIGNE
# ============================================================

elif menu == "📱 Commande en Ligne (Client)":

    st.header("📱 Passation de commande en ligne")

    st.info("Sélectionnez vos articles, précisez votre adresse et indiquez votre position sur la carte.")

    if "user_lat" not in st.session_state:
        st.session_state.user_lat = 5.3599517

    if "user_lng" not in st.session_state:
        st.session_state.user_lng = -4.0082563

    st.subheader("📍 1. Votre Position de Livraison")

    col_map1, col_map2 = st.columns([1, 2])

    with col_map1:
        if st.button("🌐 Obtenir ma position GPS automatique"):
            loc = get_geolocation()
            if loc and "coords" in loc:
                st.session_state.user_lat = loc["coords"]["latitude"]
                st.session_state.user_lng = loc["coords"]["longitude"]
                st.success("✅ Position GPS mise à jour !")

        adresse_saisie = st.text_area(
            "Précision d'adresse / Repères (Ex: Rue 12, portail vert face pharmacie)",
            key="adresse_input"
        )

    with col_map2:
        m = folium.Map(
            location=[st.session_state.user_lat, st.session_state.user_lng],
            zoom_start=14
        )

        folium.Marker(
            [st.session_state.user_lat, st.session_state.user_lng],
            popup="Lieu de livraison",
            tooltip="Position de livraison"
        ).add_to(m)

        map_data = st_folium(m, height=280, width="100%", key="map_client")

        if map_data and map_data.get("last_clicked"):
            st.session_state.user_lat = map_data["last_clicked"]["lat"]
            st.session_state.user_lng = map_data["last_clicked"]["lng"]

    st.write(
        f"📌 **Coordonnées sélectionnées :** "
        f"{st.session_state.user_lat:.5f}, {st.session_state.user_lng:.5f}"
    )

    st.markdown("---")

    st.subheader("📋 2. Vos informations et articles")

    with st.form("form_client_online", clear_on_submit=True):

        c1, c2 = st.columns(2)

        with c1:
            nom_client = st.text_input("Nom & Prénom *")
            telephone_client = st.text_input("Numéro de téléphone (WhatsApp / Appel) *")

        with c2:
            genre_client = st.selectbox("Genre", ["Homme", "Femme", "Autre"])

        st.subheader("🛒 Sélection des articles à faire nettoyer")

        articles_client = {}
        cols = st.columns(3)

        for idx, item in enumerate(TYPES_ARTICLES):
            with cols[idx % 3]:
                qte = st.number_input(f"{item}", min_value=0, step=1, key=f"online_{item}")
                if qte > 0:
                    articles_client[item] = qte

        submit_online = st.form_submit_button("Envoyer ma commande", type="primary")

    if submit_online:

        total_art = sum(articles_client.values())

        if not nom_client.strip():
            st.error("❌ Le nom est obligatoire.")
        elif not telephone_client.strip():
            st.error("❌ Le numéro de téléphone est obligatoire.")
        elif total_art == 0:
            st.error("❌ Veuillez sélectionner au moins un article.")
        else:
            conn = get_connection()
            c = conn.cursor()

            c.execute("""
                INSERT INTO commandes (
                    date_depot, nom_client, telephone_client, genre_client,
                    articles_deposes, articles_recuperes, prix_total, modalite_paiement,
                    montant_verse, statut_traitement, quantite_restante_traitement,
                    date_recuperation, statut_commande, dans_corbeille, latitude,
                    longitude, adresse_livraison
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?
                )
            """, (
                datetime.now().strftime("%Y-%m-%d"),
                nom_client,
                telephone_client,
                genre_client,
                json.dumps(articles_client),
                json.dumps({k: 0 for k in articles_client}),
                0.0,
                "À définir au ramassage",
                0.0,
                "En cours",
                total_art,
                "-",
                "En cours",
                st.session_state.user_lat,
                st.session_state.user_lng,
                adresse_saisie
            ))

            conn.commit()
            conn.close()

            st.success("✅ Votre demande a bien été enregistrée ! Notre livreur vous recontactera.")


# ============================================================
# 3. GÉNÉRATEUR DE QR CODE
# ============================================================

elif menu == "📲 Générer QR Code":

    st.header("📲 QR Code de commande en ligne")

    st.write("Imprimez ce QR Code pour vos clients.")

    url_app = st.text_input(
        "Lien de votre application web :",
        value="https://votre-app-pressing.streamlit.app"
    )

    if st.button("Générer le QR Code"):

        # Génération simplifiée du QR Code
        img = qrcode.make(url_app)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        byte_im = buf.getvalue()

        st.image(byte_im, caption="Scannez pour commander en ligne", width=250)

        st.download_button(
            label="💾 Télécharger le QR Code (PNG)",
            data=byte_im,
            file_name="qr_code_pressing.png",
            mime="image/png"
        )


# ============================================================
# 4. HISTORIQUE DES COMMANDES
# ============================================================

elif menu == "Historique des Commandes":

    st.header("📊 Historique des Commandes Actives")

    filtre = st.radio("Filtrer par statut", ["Toutes", "En cours", "Terminées"], horizontal=True)

    conn = get_connection()

    if filtre == "Toutes":
        df = pd.read_sql_query(
            "SELECT * FROM commandes WHERE dans_corbeille = 0 ORDER BY id DESC",
            conn
        )
    else:
        df = pd.read_sql_query(
            "SELECT * FROM commandes WHERE statut_commande = ? AND dans_corbeille = 0 ORDER BY id DESC",
            conn,
            params=(filtre,)
        )

    conn.close()

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

        if "adresse_livraison" in df_display.columns:
            df_display["adresse_livraison"] = df_display["adresse_livraison"].fillna("-")

        cols_to_show = [
            "id", "date_depot", "nom_client", "telephone_client",
            "Articles Déposés", "prix_total", "modalite_paiement",
            "statut_commande", "adresse_livraison", "latitude", "longitude"
        ]

        cols_to_show = [col for col in cols_to_show if col in df_display.columns]

        st.dataframe(df_display[cols_to_show], width="stretch")

        st.markdown("---")
        st.subheader("🗺 Localisation de livraison des clients en ligne")

        commandes_gps = df_display[df_display["latitude"].notnull()]

        if not commandes_gps.empty:
            cmd_sel_id = st.selectbox(
                "Sélectionner une commande avec position GPS :",
                commandes_gps["id"].tolist()
            )

            row_sel = commandes_gps[commandes_gps["id"] == cmd_sel_id].iloc[0]

            lat = row_sel["latitude"]
            lng = row_sel["longitude"]

            gmaps_url = f"https://www.google.com/maps?q={lat},{lng}"

            col_det1, col_det2 = st.columns(2)

            with col_det1:
                st.write(f"**Client :** {row_sel['nom_client']} ({row_sel['telephone_client']})")
                st.write(f"**Repère / Adresse :** {row_sel['adresse_livraison']}")
                st.write(f"**Coordonnées :** {lat:.5f}, {lng:.5f}")
                st.markdown(f"👉 [Ouvrir le trajet dans Google Maps]({gmaps_url})")

            with col_det2:
                m_view = folium.Map(location=[lat, lng], zoom_start=15)
                folium.Marker([lat, lng], popup=f"Commande #{cmd_sel_id}", tooltip=row_sel["nom_client"]).add_to(m_view)
                st_folium(m_view, height=220, width="100%", key=f"view_map_{cmd_sel_id}")
        else:
            st.info("Aucune commande avec coordonnées GPS pour le moment.")


# ============================================================
# 5. MISE À JOUR & RETRAITS
# ============================================================

elif menu == "Mise à jour & Retraits":

    st.header("📦 Mise à jour & Retraits")

    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM commandes WHERE dans_corbeille = 0 ORDER BY id DESC", conn)
    conn.close()

    if df.empty:
        st.info("Aucune commande disponible.")
    else:
        commande_id = st.selectbox("Sélectionner une commande", df["id"].tolist())
        commande = df[df["id"] == commande_id].iloc[0]

        st.write(f"**Client :** {commande['nom_client']}")
        st.write(f"**Téléphone :** {commande['telephone_client']}")
        st.write(f"**Statut actuel :** {commande['statut_commande']}")

        nouveau_statut = st.selectbox("Nouveau statut", ["En cours", "Terminées"])

        if st.button("💾 Enregistrer le nouveau statut", type="primary"):
            conn = get_connection()
            c = conn.cursor()

            c.execute("""
                UPDATE commandes
                SET statut_commande = ?, statut_traitement = ?
                WHERE id = ?
            """, (nouveau_statut, nouveau_statut, commande_id))

            conn.commit()
            conn.close()

            st.success("✅ Statut de la commande mis à jour.")
            st.rerun()


# ============================================================
# 6. DÉPENSES & BÉNÉFICE
# ============================================================

elif menu == "💸 Gestion des Dépenses & Bénéfice":

    st.header("💸 Gestion des Dépenses & Bénéfice")

    st.subheader("➕ Ajouter une dépense")

    with st.form("form_depense"):
        date_depense = st.date_input("Date de la dépense", datetime.now())
        designation = st.selectbox("Désignation", DESIGNATIONS_DEPENSES)
        montant_depense = st.number_input("Montant", min_value=0.0, step=100.0)
        commentaire = st.text_area("Commentaire")
        ajouter_depense = st.form_submit_button("Ajouter la dépense")

    if ajouter_depense:
        if montant_depense <= 0:
            st.error("❌ Le montant doit être supérieur à 0.")
        else:
            conn = get_connection()
            c = conn.cursor()

            c.execute("""
                INSERT INTO depenses (date_depense, designation, montant, commentaire)
                VALUES (?, ?, ?, ?)
            """, (str(date_depense), designation, montant_depense, commentaire))

            conn.commit()
            conn.close()

            st.success("✅ Dépense enregistrée.")
            st.rerun()

    conn = get_connection()

    total_commandes = pd.read_sql_query(
        "SELECT COALESCE(SUM(prix_total), 0) AS total FROM commandes WHERE dans_corbeille = 0",
        conn
    ).iloc[0]["total"]

    total_depenses = pd.read_sql_query(
        "SELECT COALESCE(SUM(montant), 0) AS total FROM depenses",
        conn
    ).iloc[0]["total"]

    conn.close()

    benefice = total_commandes - total_depenses

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("💰 Chiffre d'affaires", f"{total_commandes:,.0f}")
    with col2:
        st.metric("💸 Total dépenses", f"{total_depenses:,.0f}")
    with col3:
        st.metric("📈 Bénéfice", f"{benefice:,.0f}")


# ============================================================
# 7. CORBEILLE
# ============================================================

elif menu == "🗑 Corbeille (Archivées > 3 mois)":

    st.header("🗑 Corbeille - Commandes archivées")

    limite_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        UPDATE commandes
        SET dans_corbeille = 1
        WHERE date_depot < ? AND statut_commande = 'Terminées'
    """, (limite_date,))

    conn.commit()

    df_corbeille = pd.read_sql_query(
        "SELECT * FROM commandes WHERE dans_corbeille = 1 ORDER BY id DESC",
        conn
    )

    conn.close()

    if df_corbeille.empty:
        st.info("🗑 La corbeille est vide.")
    else:
        st.dataframe(df_corbeille, width="stretch")
        st.warning("Les commandes affichées ici sont archivées.")
