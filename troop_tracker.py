import logging
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

class TroopTracker:
    def __init__(self, sheet_id):
        self.sheet_id = sheet_id
        self.processed_reports = set()
        self.creds = service_account.Credentials.from_service_account_file(
            'credentials.json',
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        self.service = build('sheets', 'v4', credentials=self.creds)
        self.sheet = self.service.spreadsheets()

    def update_player_stats(self, player_name, alliance_tag, t5_dead, t6_dead):
        try:
            # Get existing data
            result = self.sheet.values().get(
                spreadsheetId=self.sheet_id,
                range='Sheet1!A:G'
            ).execute()
            values = result.get('values', [])

            # Find player row
            player_row = -1
            for i, row in enumerate(values):
                if row[0] == player_name:
                    player_row = i
                    break

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if player_row == -1:
                # Add new player
                new_row = [
                    player_name, alliance_tag, t5_dead, t6_dead,
                    t5_dead + t6_dead, 1, now
                ]
                self.sheet.values().append(
                    spreadsheetId=self.sheet_id,
                    range='Sheet1!A:G',
                    valueInputOption='USER_ENTERED',
                    body={'values': [new_row]}
                ).execute()
            else:
                # Update existing player
                current = values[player_row]
                updated_row = [
                    player_name,
                    alliance_tag,
                    int(current[2]) + t5_dead,
                    int(current[3]) + t6_dead,
                    int(current[2]) + t5_dead + int(current[3]) + t6_dead,
                    int(current[5]) + 1,
                    now
                ]
                self.sheet.values().update(
                    spreadsheetId=self.sheet_id,
                    range=f'Sheet1!A{player_row + 1}:G{player_row + 1}',
                    valueInputOption='USER_ENTERED',
                    body={'values': [updated_row]}
                ).execute()

        except HttpError as error:
            logger.error(f"Google Sheets API error: {error}")

    def is_report_processed(self, report_id):
        return report_id in self.processed_reports

    def process_battle_report(self, report_data, report_id):
        if self.is_report_processed(report_id):
            logger.info(f"Report {report_id} already processed")
            return False

        try:
            # Extract battle data
            mail_data = report_data.get('mail', {})
            battle_result = None
            
            for param in mail_data.get('param', []):
                if param.get('type') == 5:
                    battle_result = param.get('battleResult')
                    break

            if not battle_result:
                return False

            # Process troops for both sides
            delta_troops = battle_result.get('deltaTroops', [])
            
            for army_index, army in enumerate(delta_troops):
                if not army:
                    continue
                    
                # Get player info
                player_info = battle_result['before'][army_index][0]['kingdom']
                player_name = player_info.get('name', 'Unknown')
                alliance_tag = player_info.get('allianceTag', '')
                
                # Count dead troops
                t5_dead = 0
                t6_dead = 0
                
                for troop_group in army:
                    for troop in troop_group.get('troops', []):
                        code = troop.get('code')
                        dead = troop.get('dead', 0)
                        
                        # Check troop tier
                        if code in [50100105, 50100205, 50100305]:  # T5
                            t5_dead += dead
                        elif code in [50100106, 50100206, 50100306, 50100107, 50100207, 50100307]:  # T6
                            t6_dead += dead
                
                if t5_dead > 0 or t6_dead > 0:
                    self.update_player_stats(player_name, alliance_tag, t5_dead, t6_dead)

            self.processed_reports.add(report_id)
            return True

        except Exception as e:
            logger.error(f"Error processing battle report: {e}")
            return False