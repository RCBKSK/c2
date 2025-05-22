import logging
import time
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AllianceManager:

    def __init__(self, api_client):
        self.api_client = api_client
        self.last_check = time.time()
        self.check_interval = 300  # 5 minutes
        self.offline_threshold = 7200  # 2 hours in seconds
        self.auto_accept_enabled = False
        self.auto_remove_enabled = False
        self.min_power = 0
        self.required_rank = None

    def toggle_auto_accept(self,
                           enabled: bool,
                           min_power: int = 0,
                           required_rank: Optional[int] = None) -> Dict:
        """Toggle auto-accept with power and rank filters"""
        self.auto_accept_enabled = enabled
        self.min_power = min_power
        self.required_rank = required_rank
        logger.info(
            f"Auto-accept {'enabled' if enabled else 'disabled'} | Min Power: {min_power} | Required Rank: {required_rank}"
        )
        return {
            "enabled": enabled,
            "min_power": min_power,
            "required_rank": required_rank
        }

    def toggle_auto_remove(self,
                           enabled: bool,
                           hours_threshold: int = 2) -> Dict:
        """Toggle auto-remove with configurable threshold"""
        self.auto_remove_enabled = enabled
        self.offline_threshold = hours_threshold * 60 * 60  # Convert hours to seconds
        logger.info(
            f"Auto-remove {'enabled' if enabled else 'disabled'} | Threshold: {hours_threshold} hours"
        )
        return {"enabled": enabled, "hours_threshold": hours_threshold}

    async def process_join_requests(self) -> List[Dict]:
        """Process and accept/reject join requests based on settings"""
        results = []
        if not self.auto_accept_enabled:
            logger.debug("Auto-accept disabled, skipping requests")
            return results

        try:
            # Get current requests
            response = self.api_client.post('alliance/request/list')
            if not response.get('result'):
                logger.error("Failed to get request list")
                return results

            requests = response.get('requestList', [])
            logger.info(f"Processing {len(requests)} join requests")

            for request in requests:
                result = {
                    'accepted': False,
                    'name': request.get('name', 'Unknown'),
                    'power': int(request.get('power', 0)),
                    'kingdom': request.get('kingdomName', 'Unknown'),
                    'kingdom_id': request.get('_id'),
                    'reason': ''
                }

                # Validate kingdom ID
                if not result['kingdom_id']:
                    result['reason'] = "Missing kingdom ID"
                    results.append(result)
                    continue

                # Check power requirement (30M minimum)
                if result['power'] < 30000000:
                    result['reason'] = f"Minimum Power Not Met ({result['power']} < 30,000,000)"
                    try:
                        # Reject the request
                        reject_response = self.api_client.post(
                            'alliance/request/deny',
                            {"kingdomId": result['kingdom_id']})
                        
                        if not reject_response.get('result'):
                            logger.warning(f"Failed to reject {result['name']}: {reject_response.get('err', {}).get('message', 'Unknown error')}")
                    except Exception as e:
                        logger.error(f"Error rejecting request: {str(e)}")
                    
                    results.append(result)
                    continue

                # Check rank requirement
                request_rank = request.get('rank', 0)
                if self.required_rank is not None and request_rank != self.required_rank:
                    result[
                        'reason'] = f"Rank {request_rank} doesn't meet requirement ({self.required_rank})"
                    results.append(result)
                    continue

                # Accept the request
                accept_response = self.api_client.post(
                    'alliance/request/accept',
                    {"kingdomId": result['kingdom_id']})

                if accept_response.get('result'):
                    result['accepted'] = True
                    logger.info(
                        f"Accepted {result['name']} (Power: {result['power']})"
                    )
                else:
                    error = accept_response.get('err', {})
                    result['reason'] = error.get('message', 'Unknown error')
                    logger.warning(
                        f"Failed to accept {result['name']}: {result['reason']}"
                    )

                results.append(result)

        except Exception as e:
            logger.error(f"Error processing requests: {str(e)}", exc_info=True)

        return results

    async def check_inactive_players(self) -> List[Dict]:
        """Check and remove inactive players (only from Rank 1)"""
        results = []
        if not self.auto_remove_enabled:
            logger.debug("Auto-remove disabled, skipping check")
            return results

        try:
            response = self.api_client.post('alliance/members/list')
            if not response.get('result'):
                logger.error("Failed to get member list")
                return results

            current_time = datetime.now(timezone.utc)
            for rank_group in response.get('members', []):
                # Only process Rank 1 members
                if rank_group.get('_id') != 1:
                    logger.debug(f"Skipping rank {rank_group.get('_id')} members as auto-remove only targets R1")
                    continue
                    
                logger.info(f"Checking {len(rank_group.get('members', []))} members in Rank 1 for inactivity")
                
                for member in rank_group.get('members', []):
                    try:
                        last_login = datetime.fromisoformat(
                            member['lastLogined'].replace('Z', '+00:00'))
                        offline_hours = (current_time -
                                        last_login).total_seconds() / 3600

                        if offline_hours > (self.offline_threshold / 3600):
                            logger.info(f"Removing inactive R1 member: {member.get('name')} (Offline for {offline_hours:.1f} hours)")
                            remove_response = self.api_client.post(
                                'alliance/member/disband',
                                {"memberKingdomId": member['kingdomId']})

                            result = {
                                'removed':
                                remove_response.get('result', False),
                                'name': member.get('name', 'Unknown'),
                                'power': int(member.get('power', 0)),
                                'offline_hours': offline_hours,
                                'reason': ''
                            }

                            if not result['removed']:
                                error = remove_response.get('err', {})
                                result['reason'] = error.get(
                                    'message', 'Unknown error')

                            results.append(result)
                    except Exception as e:
                        logger.warning(f"Error processing member: {str(e)}")
                        continue

        except Exception as e:
            logger.error(f"Error checking inactive players: {str(e)}",
                         exc_info=True)

        return results

    async def get_alliance_status(self) -> Dict:
        """Get current alliance status"""
        status = {
            'total_members': 0,
            'online_count': 0,
            'total_power': 0,
            'ranks': {},
            'last_updated': datetime.now(timezone.utc).isoformat()
        }

        try:
            response = self.api_client.post('alliance/members/list')
            if not response.get('result'):
                return status

            for rank_group in response.get('members', []):
                members = rank_group.get('members', [])
                if not members:
                    continue

                rank_name = "Leader" if rank_group.get(
                    '_id') == 99 else f"Rank {rank_group.get('_id')}"
                online_count = sum(1 for m in members
                                   if m.get('logined', False))
                total_power = sum(int(m.get('power', 0)) for m in members)

                status['ranks'][rank_name] = {
                    'members': len(members),
                    'online': online_count,
                    'online_percent': round(
                        (online_count / len(members)) * 100, 1),
                    'avg_power': total_power // len(members)
                }

                status['total_members'] += len(members)
                status['online_count'] += online_count
                status['total_power'] += total_power

        except Exception as e:
            logger.error(f"Error getting alliance status: {str(e)}",
                         exc_info=True)

        return status

    async def get_join_requests(self) -> List[Dict]:
        """Get current join requests"""
        try:
            response = self.api_client.post('alliance/request/list')
            if response.get('result'):
                return [{
                    'name': r.get('name', 'Unknown'),
                    'power': int(r.get('power', 0)),
                    'kingdom': r.get('kingdomName', 'Unknown'),
                    'rank': r.get('rank', 0),
                    'kingdom_id': r.get('_id'),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                } for r in response.get('requestList', [])]
        except Exception as e:
            logger.error(f"Error getting join requests: {str(e)}",
                         exc_info=True)
        return []
