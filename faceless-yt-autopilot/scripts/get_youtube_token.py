"""Create the YouTube OAuth refresh token the workflows use. Run it once, on your own computer.

Before running it:
  1. In Google Cloud Console, create a project and enable "YouTube Data API v3".
  2. Set up the OAuth consent screen: External, add your Google account as a test user.
     Then set the publishing status to "In production". In "Testing" mode refresh tokens
     expire after 7 days and the autopilot stops uploading.
  3. Under Credentials, create an OAuth client ID of type "Desktop app" and download the JSON.

Then:
  pip install google-auth-oauthlib
  python scripts/get_youtube_token.py path/to/client_secret.json

A browser opens. Sign in, pick the channel (brand account) to upload to, and allow access.
The script prints the three values to store as GitHub Actions secrets.
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    client = json.load(open(sys.argv[1]))["installed"]
    print("\nAdd these as repository secrets (Settings > Secrets and variables > Actions):\n")
    print(f"YT_CLIENT_ID      = {client['client_id']}")
    print(f"YT_CLIENT_SECRET  = {client['client_secret']}")
    print(f"YT_REFRESH_TOKEN  = {creds.refresh_token}")


if __name__ == "__main__":
    main()
