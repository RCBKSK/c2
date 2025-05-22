import httpx
from datetime import datetime, timezone, timedelta
import logging
import asyncio

logger = logging.getLogger(__name__)


class DSATracker:

    def __init__(self):
        self.API_URL = "https://api-lok-beta.leagueofkingdoms.com/api/drago/dashboard/dsamine"
        self.DSA_ID = 66
        self.current_amount = 0
        self.last_update = None

    async def get_dsa_spawn(self):
        try:
            today = datetime.now(
                timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")
            logger.info(f"Fetching DSA spawn data for date: {today}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Making API request to: {self.API_URL}")
                response = await client.post(self.API_URL,
                                             json={"from": today},
                                             timeout=30.0)

                if response.status_code != 200:
                    logger.error(
                        f"API returned non-200 status code: {response.status_code}"
                    )
                    logger.error(f"Response content: {response.text}")
                    return None

                data = response.json()
                logger.info(f"DSA spawn API request successful")

                if not data.get("result"):
                    error_message = data.get("err",
                                             {}).get("message",
                                                     "Unknown error")
                    logger.error(
                        f"Failed to get DSA spawn data: {error_message}")
                    return None

                mining_data = data.get("dashboard", {}).get("todayMining", [])
                if not mining_data:
                    logger.warning("No mining data found in API response")
                    return 0

                for spawn in mining_data:
                    if spawn.get("_id") == self.DSA_ID:
                        self.current_amount = spawn.get("amount", 0)
                        self.last_update = datetime.now(timezone.utc)
                        logger.info(
                            f"Found DSA spawn amount: {self.current_amount}")
                        return self.current_amount

                logger.warning(
                    f"DSA ID {self.DSA_ID} not found in mining data")
                return 0

        except httpx.TimeoutException:
            logger.error("Timeout while fetching DSA spawn data")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error while fetching DSA spawn data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching DSA spawn data: {e}")
            return None


class LOKAPledgeTracker:

    def __init__(self):
        self.API_URL = "https://api-lok-beta.leagueofkingdoms.com/api/staking/dashboard"
        self.CONTINENT_ID = 66
        self.current_amount = 0
        self.last_update = None

    async def get_loka_pledge(self):
        try:
            logger.info(f"Fetching LOKA pledge data")

            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"Making API request to: {self.API_URL}")
                response = await client.post(self.API_URL, json={})

                if response.status_code != 200:
                    logger.error(
                        f"API returned non-200 status code: {response.status_code}"
                    )
                    logger.error(f"Response content: {response.text}")
                    return None

                data = response.json()
                logger.info(f"LOKA pledge API request successful")

                if not data.get("result"):
                    error_message = data.get("err",
                                             {}).get("message",
                                                     "Unknown error")
                    logger.error(
                        f"Failed to get LOKA pledge data: {error_message}")
                    return None

                continent_data = data.get("dashboard", {}).get("continent", [])
                if not continent_data:
                    logger.warning("No continent data found in API response")
                    return 0

                for continent in continent_data:
                    if continent.get("continent") == self.CONTINENT_ID:
                        pledge_amount = continent.get("value", 0)
                        self.current_amount = pledge_amount
                        self.last_update = datetime.now(timezone.utc)
                        logger.info(
                            f"Found LOKA pledge amount for continent {self.CONTINENT_ID}: {self.current_amount}"
                        )
                        return int(pledge_amount)

                logger.warning(
                    f"Continent ID {self.CONTINENT_ID} not found in pledge data"
                )
                return 0

        except httpx.TimeoutException:
            logger.error("Timeout while fetching LOKA pledge data")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error while fetching LOKA pledge data: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching LOKA pledge data: {e}")
            return None
