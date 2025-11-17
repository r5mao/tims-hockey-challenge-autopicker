import json
from pathlib import Path
from utils.autopicker_utils import tabulate_player_set
from nhl_api_client import NHLApiClient
from player import Player
import csv
import re
from datetime import datetime, timedelta
import argparse

project_path = Path(__file__).parent.parent
log_path = project_path / 'logs'
data_path = project_path / 'autopicker' / 'data'

team_name_fixes_file_name = data_path / 'team_name_fixes.json'
jersey_number_fixes_file_name = data_path / 'jersey_number_fixes.json'

try:
    team_name_fixes = json.load(open(team_name_fixes_file_name, 'r', encoding='utf-8'))
except Exception:
    team_name_fixes = {}

try:
    jersey_number_fixes = json.load(open(jersey_number_fixes_file_name, 'r', encoding='utf-8'))
except Exception:
    jersey_number_fixes = {}

nhl_api_client = NHLApiClient()


def run_nhl_backtest(start_date: str, end_date: str, top_n: int = 3):
    """Run backtest using NHL historical schedule/boxscore between start_date and end_date (inclusive)."""
    s_date = datetime.fromisoformat(start_date)
    e_date = datetime.fromisoformat(end_date)
    results = []

    cur = s_date
    while cur <= e_date:
        date_str = cur.strftime('%Y-%m-%d')
        print(f'Processing game week of {date_str}...')
        sched = nhl_api_client.get_schedule_for_date(date_str)

        if not sched or len(sched) == 0:
            cur += timedelta(days=1)
            continue

        # collect all players who appear in boxscores that day
        players_seen = {}
        scorers = set()
        for date_entry in sched:
            for game_week in date_entry.get('gameWeek', []):
                date = game_week.get('date')
                date_obj = datetime.strptime(date, '%Y-%m-%d').date()
                today = datetime.now().date()
                if date_obj > today:
                    print(f'Skipping future date {date}')
                    continue

                for game in game_week.get('games', []):
                    game_id = game.get('id')
                    box = nhl_api_client.get_game_boxscore(game_id)
                    game_date = box.get('gameDate', '')
                    game_state = box.get('gameState', '')
                    if game_state == 'FUT':
                        print(f'Skipping future game {game_id} on {game_date}')
                        continue

                    for team_side in ['awayTeam', 'homeTeam']:
                        team = box.get(team_side, {})
                        team_abbrev = team.get('abbrev', '')
                        team_stats = box.get('playerByGameStats', {}).get(team_side)
                        if not team_stats:
                            print(f'No player stats found for {team_side} on {date_str} in game {game_id}')
                            continue
                        for position in ['forwards', 'defense']:
                            players_list = team_stats.get(position, [])
                            # print(f'players_list: {players_list}')
                            for player in players_list:
                                player_id = player.get('playerId')
                                fname = player.get('name', '').get('default', '')
                                jersey = player.get('sweaterNumber', None)
                                pos = player.get('position', '')
                                goals = player.get('goals', 0)
                                players_seen[player_id] = {
                                    'id': player_id,
                                    'fullName': fname,
                                    'jerseyNumber': jersey,
                                    'position': pos,
                                    'teamAbbrev': team_abbrev,
                                    'goals_today': goals
                                }
                                if goals and goals > 0:
                                    scorers.add(player_id)

        # Build DataFrame-like list of player dicts using Player and populate stats
        stats_list = []
        for pid, pinfo in players_seen.items():
            try:
                fn = pinfo['fullName'].split(' ', 1)
                first = fn[0]
                last = fn[1] if len(fn) > 1 else ''
                player = Player(
                    id=pid,
                    tims_player_id=str(pid),
                    first_name=first,
                    last_name=last,
                    jersey_number=int(pinfo['jerseyNumber']) if pinfo['jerseyNumber'] else 0,
                    position=pinfo['position'],
                    team_abbr=pinfo['teamAbbrev']
                )
                nhl_api_client.populate_player_stats(player)
                player_dict = player.dict()
                # add flag whether scored that day
                player_dict['scored_today'] = (pid in scorers)
                stats_list.append(player_dict)
            except Exception as e:
                print(f'  Failed to populate stats for player {pid}: {e}')
                continue

        import pandas as pd
        df = pd.DataFrame(stats_list)
        if df.empty:
            cur += timedelta(days=1)
            continue

        # Rank using same criteria as main: goals, recent goals, goals/game
        sorted_df = df.sort_values(by=['goals', 'recent goals', 'goals/game'], ascending=[False, False, False])
        top_picks = sorted_df.head(top_n)

        picks = []
        correct = 0
        total = 0
        for _, row in top_picks.iterrows():
            picks.append({'id': row['id'], 'name': row['name'], 'scored': row['scored_today']})
            total += 1
            if row['scored_today']:
                correct += 1

        results.append({'date': date_str, 'picks': picks, 'correct': correct, 'total': total})

        cur += timedelta(days=1)

    # write CSV
    out_csv = log_path / 'backtest_nhl_results.csv'
    with open(out_csv, 'w', newline='', encoding='utf-8') as cf:
        writer = csv.writer(cf)
        header = ['date'] + [f'pick{i+1}_id' for i in range(3)] + [f'pick{i+1}_name' for i in range(3)] + ['correct', 'total']
        writer.writerow(header)
        for r in results:
            row = [r['date']]
            for i in range(3):
                if i < len(r['picks']):
                    row.append(r['picks'][i]['id'])
                else:
                    row.append('')
            for i in range(3):
                if i < len(r['picks']):
                    row.append(r['picks'][i]['name'])
                else:
                    row.append('')
            row.append(r['correct'])
            row.append(r['total'])
            writer.writerow(row)

    print(f'NHL backtest complete. Results written to {out_csv}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', help='Start date YYYY-MM-DD', required=False)
    parser.add_argument('--end', help='End date YYYY-MM-DD', required=False)
    parser.add_argument('--top', help='Top N picks per day', type=int, default=3)
    args = parser.parse_args()

    if args.start and args.end:
        start = args.start
        end = args.end
    else:
        # default last 14 days
        end_dt = datetime.today()
        start_dt = end_dt - timedelta(days=14)
        start = start_dt.strftime('%Y-%m-%d')
        end = end_dt.strftime('%Y-%m-%d')

    run_nhl_backtest(start, end, top_n=args.top)


if __name__ == '__main__':
    main()
