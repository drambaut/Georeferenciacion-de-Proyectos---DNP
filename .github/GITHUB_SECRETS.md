GitHub Actions secrets to create for this project:

- `AZURE_STORAGE_CONNECTION_STRING`
- `AZURE_CONTAINER`
- `OPENEO_AUTH_METHOD`
- `OPENEO_AUTH_CLIENT_ID`
- `OPENEO_AUTH_CLIENT_SECRET`
- `OPENEO_AUTH_PROVIDER_ID`
- `PROJECT_METADATA_XLSX_URL`
- `PROJECT_METADATA_SHEET_NAME`

Suggested values:

- `OPENEO_AUTH_METHOD=client_credentials`
- `OPENEO_AUTH_PROVIDER_ID=CDSE`
- `PROJECT_METADATA_SHEET_NAME=proyectos_satview`

How to add them in GitHub:

1. Go to `Settings` in the repository.
2. Open `Secrets and variables` > `Actions`.
3. Click `New repository secret`.
4. Create each secret with the exact names listed above.
