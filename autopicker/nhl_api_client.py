import requests
from requests.exceptions import HTTPError
import json
import logging
import time
from utils.logger_utils import log_http_error
from datetime import datetime, timedelta
from collections import Counter
from pathlib import Path
from player import Player
from typing import List, Dict, Set, Any

project_path = Path(__file__).parent.parent

logger = logging.getLogger(__name__)

# Compute NHL season string dynamically so the code works across season boundaries.
# Format used elsewhere in the code is YYYYYYYY (e.g. '20242025').
now = datetime.now()
# NHL seasons typically start in the fall (around Oct). Use July as a safe cutoff.
if now.month >= 7:
    start_year = now.year
else:
    start_year = now.year - 1
SEASON = f"{start_year}{start_year + 1}"

class NHLApiClient:
    def __init__(self):
        self.injured_player_names: Set[str] = self._get_injured_player_names()
        # self.recent_goal_scorers: Counter = self._get_recent_goal_scorers()
        time_range: int = 5
    
    def get_teams(self) -> List[Dict[str, Any]]:
        try:
            response = requests.get('https://api.nhle.com/stats/rest/en/team')
            response.raise_for_status()
            return response.json()['data']
        except HTTPError as http_err:
            error_msg = f"HTTP error occured when trying to obtain list of NHL teams"
            log_http_error(error_msg, logger, response, http_err)

    def get_team_roster(self, team_abbr: str) -> Dict[str, Any]:
        try:
            response = requests.get(f'https://api-web.nhle.com/v1/roster/{team_abbr}/{SEASON}')
            response.raise_for_status()
            return response.json()
        except HTTPError as http_err:
            error_msg = f"HTTP error occured when trying to obtain {team_abbr}'s roster"
            log_http_error(error_msg, logger, response, http_err)

    def populate_player_stats(self, player: Player) -> None:
        try:
            response = requests.get(f'https://api-web.nhle.com/v1/player/{player.id}/landing')
            response.raise_for_status()
            player_data = response.json()

            # Look for season totals for the current seasons in the NHL league
            curr_season_totals = None
            for season_total in reversed(player_data['seasonTotals']): # start looking from back (most recent)
                if season_total['season'] != int(SEASON):
                    break
                if season_total['leagueAbbrev'] == 'NHL':
                    curr_season_totals = season_total
                    break

            if curr_season_totals is None:
                logger.error(f'Unable to pull current {SEASON} season totals for {player.full_name} ({player.id})')
                return
            
            for game in player_data['last5Games']:
                player.recent_goals += game['goals']
            
            player.goals = curr_season_totals['goals']
            player.points = curr_season_totals['points']
            player.shots = curr_season_totals['shots']
            player.shot_percentage = curr_season_totals['shootingPctg']
            player.plus_minus = curr_season_totals['plusMinus']
            m, s = curr_season_totals['avgToi'].split(':')
            player.time_on_ice = timedelta(minutes=int(m), seconds=int(s))
            player.games_played = curr_season_totals['gamesPlayed']
            player.goals_per_game = round(1.0 * player.goals/player.games_played, 2)
            player.injured = player.full_name in self.injured_player_names
            
            logger.debug(f"Obtained {player.full_name} ({player.id})'s stats")
        except HTTPError as http_err:
            error_msg = f"HTTP error occurred when trying to trying to obtain {player.full_name} ({player.id})'s stats"
            log_http_error(error_msg, logger, response, http_err)
        except Exception as e:
            logger.error(f"An unexpected error occurred when populating {player.full_name} ({player.id})'s stats: {e}")
            exit()
            
    def _get_injured_player_names(self) -> Set[str]:
        try:
            response = requests.get('https://www.rotowire.com/hockey/tables/injury-report.php?team=ALL&pos=ALL')
            response.raise_for_status()
            logger.debug('Obtained list of injured players')
            return {f"{injured['firstname']} {injured['lastname']}" for injured in response.json()}
        except HTTPError as http_err:
            error_msg = 'HTTP error occured when trying to find list of injured players'
            log_http_error(error_msg, logger, response, http_err)

    def _get_recent_goal_scorers(self):
        start_date = (datetime.today() - timedelta(days=Player.time_range)).strftime('%Y-%m-%d')
        end_date = datetime.today().strftime('%Y-%m-%d')
        url = f'https://nhl-score-api.herokuapp.com/api/scores?startDate={start_date}&endDate={end_date}'
        for tries in range(1, 4):
            try:
                response = requests.get(url)
                response.raise_for_status()
                break
            except HTTPError as http_error:
                if tries == 3:
                    error_msg = f'HTTP error occured when trying to get recent goal scorers from the past {Player.time_range} days'
                    log_http_error(error_msg, logger, response, http_error)
        
        goal_scorers = []
        for date in response.json():
            for game in date['games']:
                if game['status']['state'] == 'FINAL':
                    for goal in game['goals']:
                        goal_scorers.append(goal['scorer']['player'])
        logger.debug('Obtained list of recent goal scorers')
        return Counter(goal_scorers)

    # --- New methods for historical data / backtesting ---
    def get_schedule_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Return NHL schedule for a given date (YYYY-MM-DD) using the public statsapi."""
        url = f'https://api-web.nhle.com/v1/schedule/{date_str}'
        for attempt in range(1, 4):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                # Normalize response: prefer 'dates' list, otherwise return list or wrap single dict
                if isinstance(data, dict) and 'dates' in data:
                    return data.get('dates', [])
                if isinstance(data, list):
                    return data
                return [data]
            except HTTPError as http_err:
                error_msg = f'HTTP error occurred when trying to obtain schedule for {date_str} (attempt {attempt})'
                log_http_error(error_msg, logger, response, http_err)
                return []
            except requests.exceptions.RequestException as req_err:
                logger.warning(f'Network error when fetching schedule for {date_str} (attempt {attempt}): {req_err}')
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
                    continue
                return []

    def get_game_boxscore(self, game_id: int) -> Dict[str, Any]:
        """Return boxscore JSON for a given game_id."""
        # New NHL API base for boxscore
        url = f'https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore'
        for attempt in range(1, 4):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                return response.json()
            except HTTPError as http_err:
                error_msg = f'HTTP error occurred when trying to obtain boxscore for game {game_pk} (attempt {attempt})'
                log_http_error(error_msg, logger, response, http_err)
                return {}
            except requests.exceptions.RequestException as req_err:
                logger.warning(f'Network error when fetching boxscore for game {game_pk} (attempt {attempt}): {req_err}')
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
                    continue
                return {}

    def get_players_in_game(self, game_pk: int) -> List[Dict[str, Any]]:
        """Return a list of players who appear in the boxscore for the game (skaters only)."""
        box = self.get_game_boxscore(game_pk)
        players = []
        try:
            teams = box.get('teams', {})
            for side in ['away', 'home']:
                team = teams.get(side, {})
                team_abbrev = team.get('team', {}).get('abbreviation')
                for pid, pdata in team.get('players', {}).items():
                    person = pdata.get('person', {})
                    stats = pdata.get('stats', {})
                    # only include skaters with skaterStats
                    if 'skaterStats' in stats:
                        player_entry = {
                            'id': person.get('id'),
                            'fullName': person.get('fullName'),
                            'jerseyNumber': person.get('jerseyNumber'),
                            'position': person.get('primaryPosition', {}).get('code'),
                            'teamAbbrev': team_abbrev,
                            'gameStats': stats.get('skaterStats', {})
                        }
                        players.append(player_entry)
        except Exception as e:
            logger.error(f'Error parsing boxscore players for game {game_pk}: {e}')
        return players
