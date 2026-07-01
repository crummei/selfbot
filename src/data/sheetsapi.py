from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import os
from src.paths import DATA_DIR

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SPREADSHEET_ID = "1Q746-ZX_a7Px39QP8pJ1KMkJyVsi_aIVOKNAkOxZGV4"
RANGES = ["Prompts!2:2"]
SERVICE_ACCOUNT_FILE = os.path.join(DATA_DIR, "google_api_serviceaccount.json")

def get_column_letter(n):
    """Converts a 0-based index to a lowercase spreadsheet column string (a, b... z, aa, ab...)."""
    result = ""
    while n >= 0:
        result = chr(97 + (n % 26)) + result
        n = n // 26 - 1
    return result

def main():
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build("sheets", "v4", credentials=creds)

        result = service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=RANGES[0]).execute()
        values = result.get("values", [["No data"]])[0]

        # Use the helper function to generate the correct column letters
        return {f"{get_column_letter(i)}2": value for i, value in enumerate(values)}

    except HttpError as err:
        print(f"An error occurred: {err}")
        return None

if __name__ == "__main__":
    print(main())
