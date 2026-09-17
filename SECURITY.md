# Security

Campaign Intelligence is a local, single-user prototype. Do not import customer
names, phone numbers, access tokens, production database credentials, or other
sensitive data.

## Model credentials

Model API keys are entered in the local settings screen and stored in the local
database. The database, `.env` files, logs, imported CSV files, and generated
runtime data are excluded from Git.

If a key is ever committed or included in a screenshot, revoke it at the model
provider immediately and replace it with a new key. Removing the text from the
latest commit is not sufficient because Git history and caches may retain it.

## Reporting

Please report security issues privately to the repository owner rather than
opening a public issue containing credentials or customer data.
