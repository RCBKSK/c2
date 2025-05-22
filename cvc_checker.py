
import httpx
import logging
from datetime import datetime, timezone
import json

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class CvcChecker:
    def __init__(self):
        self.API_URL = "https://api-lok-live.leagueofkingdoms.com/api/event/list/cvc"
        
    async def check_cvc_events(self, api_client):
        try:
            logger.info("Checking CvC events...")
            response = api_client.post('event/list/cvc')
            
            if isinstance(response, dict):
                # Pretty print the full response structure
                logger.info("Full CvC Response Structure:")
                print(json.dumps(response, indent=2))
                
                # Parse specific CvC details if available
                if 'events' in response:
                    for event in response['events']:
                        print("\nEvent Details:")
                        print(f"Event ID: {event.get('_id')}")
                        print(f"Start Time: {event.get('startTime')}")
                        print(f"End Time: {event.get('endTime')}")
                        print(f"Status: {event.get('status')}")
                        print(f"Season: {event.get('season')}")
                        
                        # Print rankings if available
                        if 'ranking' in event:
                            print("\nRankings:")
                            for rank in event['ranking']:
                                print(f"Rank {rank.get('rank')}: {rank.get('name')} - Score: {rank.get('score')}")
                
                return response
            else:
                logger.error(f"Invalid response format: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error checking CvC events: {e}")
            return None

if __name__ == "__main__":
    from title_bot import LokBotApi, get_valid_token
    import asyncio
    
    async def test_cvc():
        print("Getting token...")
        token = get_valid_token()
        if token:
            print("Token obtained, initializing API client...")
            api_client = LokBotApi(token)
            
            print("Checking CvC events...")
            checker = CvcChecker()
            events = await checker.check_cvc_events(api_client)
            
        else:
            print("Could not get valid token")

    # Run the test
    asyncio.run(test_cvc())
