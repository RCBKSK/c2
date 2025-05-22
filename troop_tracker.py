
import pandas as pd
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

class TroopTracker:
    def __init__(self, excel_file="troop_deaths.xlsx"):
        self.excel_file = excel_file
        self.processed_reports = set()
        try:
            self.df = pd.read_excel(excel_file)
        except FileNotFoundError:
            self.df = pd.DataFrame(columns=[
                'Player', 'Alliance', 'T5_Deaths', 'T6_Deaths', 
                'Total_Deaths', 'Battle_Count', 'Last_Updated'
            ])
            self.df.to_excel(excel_file, index=False)

    def is_report_processed(self, report_id):
        return report_id in self.processed_reports

    def update_player_stats(self, player_name, alliance_tag, t5_dead, t6_dead):
        player_row = self.df[self.df['Player'] == player_name]
        
        if player_row.empty:
            new_row = {
                'Player': player_name,
                'Alliance': alliance_tag,
                'T5_Deaths': t5_dead,
                'T6_Deaths': t6_dead,
                'Total_Deaths': t5_dead + t6_dead,
                'Battle_Count': 1,
                'Last_Updated': datetime.now()
            }
            self.df = pd.concat([self.df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            idx = player_row.index[0]
            self.df.at[idx, 'T5_Deaths'] += t5_dead
            self.df.at[idx, 'T6_Deaths'] += t6_dead
            self.df.at[idx, 'Total_Deaths'] = self.df.at[idx, 'T5_Deaths'] + self.df.at[idx, 'T6_Deaths']
            self.df.at[idx, 'Battle_Count'] += 1
            self.df.at[idx, 'Last_Updated'] = datetime.now()
            self.df.at[idx, 'Alliance'] = alliance_tag

        self.df.to_excel(self.excel_file, index=False)

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
