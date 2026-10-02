# --- 1. NOUVELLE COMMANDE ---
# Utilisation d'un formulaire nativement réinitialisable
with st.form("form_nouvelle_commande", clear_on_submit=True):
    st.header("📝 Enregistrer une nouvelle commande")

    col1, col2 = st.columns(2)
    with col1:
        # Champ 1 : Nom du client
        nom_client = st.text_input("Nom du client *")
        # Champ 2 : Numéro de téléphone placé DIRECTEMENT SOUS LE NOM DU CLIENT
        telephone_client = st.text_input("Numéro de téléphone *")

        genre_client = st.selectbox("Genre du client", ["Homme", "Femme", "Autre"])
        date_depot = st.date_input("Date de dépôt", datetime.now())

    with col2:
        prix_total = st.number_input("Prix total (€ ou FCFA) *", min_value=0.0, step=100.0)
        modalite = st.radio("Modalité de paiement *", ["Soldé", "Acompte"])

        # Pour l'acompte dans un st.form, on affiche le champ directement
        montant_verse_input = st.number_input("Montant versé (Acompte si applicable)", min_value=0.0, step=100.0)

    st.subheader("🛒 Sélection des articles *")
    articles_selectionnes = {}

    cols = st.columns(3)
    for idx, item in enumerate(TYPES_ARTICLES):
        with cols[idx % 3]:
            qte = st.number_input(f"{item}", min_value=0, step=1, key=f"depot_{item}")
            if qte > 0:
                articles_selectionnes[item] = qte

    # Bouton de soumission officiel du formulaire
    submitted = st.form_submit_button("Valider la commande", type="primary")

# Traitement lors du clic sur Valider
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
                    date_depot, nom_client, telephone_client, genre_client, articles_deposes, 
                    articles_recuperes, prix_total, modalite_paiement, 
                    montant_verse, statut_traitement, quantite_restante_traitement, 
                    date_recuperation, statut_commande, dans_corbeille
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
