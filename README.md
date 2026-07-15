# Vinícius de Paula — publication feed

A small, portable publication-data repository for `0000-0002-8255-9494`.

Its job is intentionally narrow:

1. read public works from ORCID;
2. normalise them into simple JSON;
3. publish `publications.json` and `counts.json`;
4. let any static website consume those files.

## Repository structure

```text
.
├── .github/workflows/sync-orcid.yml
├── config.json
├── data/overrides.json
├── docs/
│   ├── index.html
│   ├── publications.json
│   └── counts.json
├── scripts/sync_orcid.py
└── requirements.txt
```

## 1. Register ORCID Public API credentials

Sign in to ORCID and open **Developer Tools**.

Create a Public API application. A practical configuration is:

- **Name:** Vinícius de Paula Publication Feed
- **Application URL:** your GitHub repository URL
- **Description:** Reads my public ORCID works to maintain my personal academic website publication record.
- **Redirect URI:** use an HTTPS URL you control. The current script only uses the client-credentials `/read-public` flow and does not perform interactive login, but ORCID's application form may require a redirect URI.

Save the application and copy the **Client ID** and **Client Secret**.

Never commit the client secret to this repository.

## 2. Add the ORCID credentials to GitHub

In the repository:

**Settings → Secrets and variables → Actions → New repository secret**

Create:

- `ORCID_CLIENT_ID`
- `ORCID_CLIENT_SECRET`

## 3. Enable GitHub Pages

In the repository:

**Settings → Pages**

Under **Build and deployment**:

- Source: **Deploy from a branch**
- Branch: **main**
- Folder: **/docs**

Save.

The preview will become available at a URL shaped like:

```text
https://YOUR-GITHUB-USERNAME.github.io/REPOSITORY-NAME/
```

The data endpoints will be:

```text
https://YOUR-GITHUB-USERNAME.github.io/REPOSITORY-NAME/publications.json
https://YOUR-GITHUB-USERNAME.github.io/REPOSITORY-NAME/counts.json
```

## 4. Run the first sync manually

Open:

**Actions → Sync ORCID works → Run workflow**

The workflow obtains a `/read-public` token, reads the public ORCID `/works` section, fetches each chosen work, normalises the metadata and commits changed JSON files.

It is also scheduled for the first day of each month.

## 5. Correct classification without touching Python

Edit `data/overrides.json`.

Keys can be either a DOI or an ORCID put code.

Example:

```json
{
  "10.1021/acssuschemeng.4c09545": {
    "category": "articles"
  },
  "123456789": {
    "category": "posters",
    "venue": "Jornadas do CICECO 2025"
  }
}
```

Supported override fields include:

- `category`
- `title`
- `year`
- `venue`
- `url`
- `hidden`

Useful categories for the current website are:

- `articles`
- `oral-communications`
- `posters`
- `conference-abstracts`
- `theses`
- `dissemination`
- `other`

## 6. Later: connect Bootstrap Studio

The final Bootstrap Studio publication page only needs an empty container such as:

```html
<div id="publication-list"></div>
```

and a JavaScript file that fetches `publications.json` and creates the visual cards/list items.

The design remains in Bootstrap Studio. The publication record remains here.

## Important note about scheduled workflows

GitHub may automatically disable scheduled workflows in a public repository after 60 days without repository activity. The manual **Run workflow** button remains a useful fallback; repository activity or re-enabling the workflow restores the schedule.

## Local testing

Set environment variables for your ORCID credentials, then run:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python scripts/sync_orcid.py
python -m http.server 8000 --directory docs
```

Open `http://localhost:8000`.

Do not open `docs/index.html` directly with `file://`; browser fetch restrictions can prevent the JSON file from loading.
