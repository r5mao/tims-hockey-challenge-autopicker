import json
from pathlib import Path
import csv
from collections import defaultdict

project_path = Path(__file__).parent.parent
log_path = project_path / 'logs'
history_file = log_path / 'history.json'


def evaluate_history():
    with open(history_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    history = data.get('history', [])

    total_days = 0
    total_picks = 0
    total_correct = 0
    per_set = defaultdict(lambda: {'correct': 0, 'total': 0})
    per_day_details = []

    for entry in history:
        picks = entry.get('picks', [])
        # consider only days where picks include 'correct' flags
        if not picks:
            continue
        # check if any pick has 'correct' key
        if not any('correct' in p for p in picks):
            continue

        total_days += 1
        day_correct = 0
        day_total = 0
        for p in picks:
            if 'setId' not in p:
                continue
            set_id = str(p.get('setId'))
            correct = bool(p.get('correct', False))
            per_set[set_id]['total'] += 1
            per_set[set_id]['correct'] += 1 if correct else 0
            day_total += 1
            day_correct += 1 if correct else 0

        total_picks += day_total
        total_correct += day_correct
        per_day_details.append({
            'date': entry.get('contestDate'),
            'total': day_total,
            'correct': day_correct
        })

    overall_accuracy = (total_correct / total_picks) if total_picks else 0

    # write summary csv
    summary_file = log_path / 'evaluation_summary.csv'
    with open(summary_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['metric', 'value'])
        writer.writerow(['days_evaluated', total_days])
        writer.writerow(['total_picks', total_picks])
        writer.writerow(['total_correct', total_correct])
        writer.writerow(['overall_accuracy', overall_accuracy])
        for set_id, vals in sorted(per_set.items()):
            acc = vals['correct'] / vals['total'] if vals['total'] else 0
            writer.writerow([f'set_{set_id}_accuracy', acc])

    # write details per day
    details_file = log_path / 'evaluation_details.csv'
    with open(details_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['date', 'total_picks', 'correct'])
        for d in per_day_details:
            writer.writerow([d['date'], d['total'], d['correct']])

    print(f'Evaluation complete. Summary: {summary_file}\nDetails: {details_file}')


if __name__ == '__main__':
    evaluate_history()
