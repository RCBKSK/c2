
import logging
import gspread
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

class TroopTracker:
    def __init__(self, spreadsheet_url):
        self.spreadsheet_url = spreadsheet_url
        self.processed_reports = set()
        # Connect to Google Sheet
        try:
            self.gc = gspread.service_account()
            self.sheet = self.gc.open_by_url(spreadsheet_url)
            self.worksheet = self.sheet.sheet1
        except Exception as e:
            logger.error(f"Failed to connect to Google Sheet: {e}")
            # Fallback to Excel
            self.worksheet = None

    def update_player_stats(self, player_name, alliance_tag, t5_dead, t6_dead):
        try:
            if self.worksheet:
                # Find player row
                try:
                    cell = self.worksheet.find(player_name)
                    row = cell.row
                except:
                    # Add new row
                    row = len(self.worksheet.get_all_values()) + 1
                
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if row == 1:
                    # New sheet, add headers
                    self.worksheet.append_row([
                        "Player", "Alliance", "T5 Dead", "T6 Dead", 
                        "Total Dead", "Reports", "Last Updated"
                    ])
                    row += 1

                if row > 1:
                    current = self.worksheet.row_values(row) if row <= self.worksheet.row_count else ["", "", "0", "0", "0", "0", ""]
                    # Update row
                    self.worksheet.update(f'A{row}:G{row}', [[
                        player_name,
                        alliance_tag,
                        int(current[2] or 0) + t5_dead,
                        int(current[3] or 0) + t6_dead,
                        int(current[2] or 0) + t5_dead + int(current[3] or 0) + t6_dead,
                        int(current[5] or 0) + 1,
                        now
                    ]])
            else:
                # Fallback to Excel file
                df = pd.read_excel("troop_deaths.xlsx")
                player_idx = df[df['Player'] == player_name].index
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if len(player_idx) == 0:
                    df = pd.concat([df, pd.DataFrame([{
                        'Player': player_name,
                        'Alliance': alliance_tag,
                        'T5 Dead': t5_dead,
                        'T6 Dead': t6_dead,
                        'Total Dead': t5_dead + t6_dead,
                        'Reports': 1,
                        'Last Updated': now
                    }])], ignore_index=True)
                else:
                    idx = player_idx[0]
                    df.loc[idx, 'T5 Dead'] += t5_dead
                    df.loc[idx, 'T6 Dead'] += t6_dead
                    df.loc[idx, 'Total Dead'] = df.loc[idx, 'T5 Dead'] + df.loc[idx, 'T6 Dead']
                    df.loc[idx, 'Reports'] += 1
                    df.loc[idx, 'Last Updated'] = now
                
                df.to_excel("troop_deaths.xlsx", index=False)

        except Exception as e:
            logger.error(f"Error updating stats: {e}")

    def process_battle_report(self, report_data, report_id):
        if report_id in self.processed_reports:
            logger.info(f"Report {report_id} already processed")
            return False

        try:
            mail_data = report_data.get('mail', {})
            battle_result = None
            
            for param in mail_data.get('param', []):
                if param.get('type') == 5:
                    battle_result = param.get('battleResult')
                    break

            if not battle_result:
                return False

            delta_troops = battle_result.get('deltaTroops', [])
            
            for army_index, army in enumerate(delta_troops):
                if not army:
                    continue
                    
                player_info = battle_result['before'][army_index][0]['kingdom']
                player_name = player_info.get('name', 'Unknown')
                alliance_tag = player_info.get('allianceTag', '')
                
                t5_dead = 0
                t6_dead = 0
                
                for troop_group in army:
                    for troop in troop_group.get('troops', []):
                        code = troop.get('code')
                        dead = troop.get('dead', 0)
                        
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
