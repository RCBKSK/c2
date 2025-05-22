
import httpx
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class TaskChecker:
    def __init__(self):
        self.API_URL = "https://lok-api-live.leagueofkingdoms.com/api/kingdom/task/all"
        self.last_check = None

    async def check_tasks(self, api_client):
        try:
            if not api_client:
                logger.error("API client not initialized")
                return None

            logger.info("Checking kingdom tasks...")
            logger.info("Attempting to check tasks...")
            
            response = api_client.post('kingdom/task/all')
            
            # Verify response is valid JSON
            if isinstance(response, dict):
                if not response.get('result'):
                    error = response.get('err', {})
                    logger.error(f"Failed to get tasks: {error}")
                    return None
            else:
                logger.error(f"Invalid response format: {response}")
                return None

            tasks = response.get('tasks', [])
            self.last_check = datetime.now(timezone.utc)
            return tasks

        except Exception as e:
            logger.error(f"Error checking tasks: {e}")
            return None

if __name__ == "__main__":
    import asyncio
    from title_bot import LokBotApi, get_valid_token

    async def test_tasks():
        print("Getting token...")
        token = get_valid_token()
        if token:
            print("Token obtained, initializing API client...")
            api_client = LokBotApi(token)

            print("Checking tasks...")
            checker = TaskChecker()
            tasks = await checker.check_tasks(api_client)

            if tasks:
                print(f"\nFound {len(tasks)} tasks:")
                for task in tasks:
                    print(f"- {task}")
            else:
                print("No tasks found or error occurred")
        else:
            print("Could not get valid token")

    # Run the test
    asyncio.run(test_tasks())
