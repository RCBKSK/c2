import httpx
import logging
import json
from datetime import datetime, timezone
from title_bot import LokBotApi, get_valid_token
import asyncio
from troop_tracker import TroopTracker

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ChatChecker:
    def __init__(self):
        self.API_URL = "https://api-lok-live.leagueofkingdoms.com/api/chat/logs"
        self.MAIL_URL = "https://api-lok-live.leagueofkingdoms.com/api/mail/read"
        self.troop_tracker = TroopTracker("troop_deaths.xlsx")

    def get_troop_tier(self, troop_code):
        # Tier 5 troops
        tier_5_troops = [50100105, 50100205, 50100305]
        # Tier 6 troops
        tier_6_troops = [50100106, 50100107, 50100206, 50100207, 50100306, 50100307]

        if troop_code in tier_5_troops:
            return "T5"
        elif troop_code in tier_6_troops:
            return "T6"
        else:
            return "Other"

    def log_troops_lost(self, response, filename="troops_lost_log.txt", excel_file="Troops.xlsx"):
        try:
            if not response or 'mail' not in response:
                return False

            mail_data = response['mail']
            battle_result = None

            # Find battle result in params
            for param in mail_data.get('param', []):
                if param.get('type') == 5:
                    battle_result = param.get('battleResult')
                    break

            if not battle_result:
                return False

            with open(filename, 'a') as f:
                # Write header if file is empty
                if f.tell() == 0:
                    f.write("Time,Kingdom ID,Name,Alliance Tag,Dead Troops (Code:Amount),Power Lost\n")

                delta_troops = battle_result.get('deltaTroops', [])
                power_lost = battle_result.get('deltaPower', [0, 0])
                battle_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # First army (usually attacker)
                if len(delta_troops) > 0:
                    k1_id = battle_result['before'][0][0]['kingdom']['_id']
                    k1_name = battle_result['before'][0][0]['kingdom']['name']
                    k1_troops = []
                    for troop_group in delta_troops[0]:
                        for troop in troop_group.get('troops', []):
                            k1_troops.append(f"{troop['code']}:{troop['dead']}")

                    # Second army (usually defender)
                    k2_id = battle_result['before'][1][0]['kingdom']['_id']
                    k2_name = battle_result['before'][1][0]['kingdom']['name']
                    k2_troops = []
                    for troop_group in delta_troops[1]:
                        for troop in troop_group.get('troops', []):
                            k2_troops.append(f"{troop['code']}:{troop['dead']}")

                    # Get alliance tags
                    k1_alliance = battle_result['before'][0][0]['kingdom'].get('allianceTag', '')
                    k2_alliance = battle_result['before'][1][0]['kingdom'].get('allianceTag', '')

                    # Write to text log
                    f.write(f"{battle_time},{k1_id},{k1_name},{k1_alliance},{';'.join(k1_troops)},{power_lost[0]}\n")
                    f.write(f"{battle_time},{k2_id},{k2_name},{k2_alliance},{';'.join(k2_troops)},{power_lost[1]}\n")

                    # Write to Excel
                    try:
                        from openpyxl import load_workbook
                        wb = load_workbook(excel_file)
                        ws = wb.active

                        # Process troops for both kingdoms
                        for kingdom_data in [(k1_troops, k1_name, k1_id), (k2_troops, k2_name, k2_id)]:
                            troops, name, kid = kingdom_data
                            for troop in troops:
                                code, dead = map(int, troop.split(':'))
                                tier = self.get_troop_tier(code)
                                ws.append([
                                    battle_time,
                                    kid,
                                    name,
                                    code,
                                    tier,
                                    dead
                                ])

                        wb.save(excel_file)
                    except Exception as e:
                        logger.error(f"Error writing to Excel: {e}")

            return True

        except Exception as e:
            logger.error(f"Error logging troops lost: {e}")
            return False

    async def read_mail(self, api_client, mail_id):
        try:
            logger.info(f"Reading mail with ID: {mail_id}")
            payload = {
                "mailId": mail_id,
                "sent": False
            }

            response = api_client.post('mail/read', payload)
            print("\nMail Read Response for ID:", mail_id)
            print(json.dumps(response, indent=2, ensure_ascii=False))

            # Log mail response to file
            log_filename = f"mail_response_{mail_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(log_filename, 'w', encoding='utf-8') as f:
                json.dump(response, f, indent=2, ensure_ascii=False)
            logger.info(f"Mail response logged to {log_filename}")

            # Process battle report for troop tracking
            if response and isinstance(response, dict):
                self.log_troops_lost(response)
                self.troop_tracker.process_battle_report(response, mail_id)

            return response

        except Exception as e:
            logger.error(f"Error reading mail: {e}")
            return None

    async def check_chat_logs(self, api_client):
        try:
            logger.info("Checking chat logs...")
            payload = {
                "chatChannel": "p67d1a11ca807992603b5fe40-6169a073852452726c48fe93"
            }

            response = api_client.post('chat/logs', payload)

            # Pretty print the response
            print("\nChat Logs Response:")
            print(json.dumps(response, indent=2))

            return response

        except Exception as e:
            logger.error(f"Error checking chat logs: {e}")
            return None

if __name__ == "__main__":
    async def test_chat():
        print("Getting token...")
        token = get_valid_token()
        if token:
            print("Token obtained, initializing API client...")
            api_client = LokBotApi(token)

            print("Checking chat logs...")
            checker = ChatChecker()
            logs = await checker.check_chat_logs(api_client)

            if logs and logs.get('result'):
                chat_logs = logs.get('list', [])
                for chat in chat_logs:
                    content = chat.get('content', {})
                    if isinstance(content, dict):
                        mail_id = content.get('mail', {}).get('id')
                        if mail_id:
                            print(f"\nReading mail from chat, ID: {mail_id}")
                            mail_response = await checker.read_mail(api_client, mail_id)

        else:
            print("Could not get valid token")

    # Run the test
    asyncio.run(test_chat())