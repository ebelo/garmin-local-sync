# Personal API coverage

The official Personal API 1.0.0 reference exposes 26 GET operations.
`sync --all-data` requests all of them within the selected/history-discovered date
interval and for all indexed activities, including multisport children. Authentication
is separate. Empty responses are successful checkpoints; errors remain visible.

| GET endpoint | Storage |
| --- | --- |
| `/activity-service/activity/{activityId}` | `activities.detail_json` |
| `/activity-service/activity/{activityId}/workouts` | `api_responses`: `activity_workouts`, keyed by activity ID |
| `/activitylist-service/activities/search/activities` | `activities.summary_json` |
| `/biometric-service/heartRateZones/v2` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/download-service/files/activity/{activityId}` | Original archive; decoded samples, FIT messages and geography |
| `/fitnessage-service/fitnessage/v2/{calendarDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/goal-service/goal/user/effective/weightgoal/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/hrv-service/hrv/daily/{fromDate}/{toDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/hrv-service/hrv/{calendarDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/nutrition-service/food/logs/{date}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/periodichealth-service/menstrualcycle/dayview/{calendarDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/periodichealth-service/menstrualcycle/summary/{calendarDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/sleep-service/sleep/dailySleep` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/sleep-service/sleep/dailySleepSummariesByDate` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/userprofile-service/userprofile/settings` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/usersummary-service/stats/hydration/daily/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/usersummary-service/stats/steps/daily/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/usersummary-service/stats/stress/daily/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/usersummary-service/usersummary/daily` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/usersummary-service/v2/dailysummaries` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/weight-service/weight/all/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/weight-service/weight/range/{startDate}/{endDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/wellness-service/wellness/bodyBattery/reports/daily` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/wellness-service/wellness/daily/spo2acclimation/{calendarDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/wellness-service/wellness/dailyHeartRate` | `api_responses`, `wellness_records`, numeric `daily_metrics` |
| `/wellnessactivity-service/activity/summary/range/{fromDate}/{untilDate}` | `api_responses`, `wellness_records`, numeric `daily_metrics` |

## Request scope and fidelity

- `--all-data` adds all nine daily-detail datasets, performed activity workouts and
  child activities. All eleven date-range datasets are also fetched.
- `--start` / `--end` limit health dates, not activities. Without `--start`, range
  daily summaries scan from the earliest activity; detailed wearable datasets start
  at the earliest daily summary with `includesWellnessData=true`. Weight history
  retains the earlier activity-based bound. Explicit `--start` overrides discovery.
- Arrays (including all-day HR/HRV/SpO2 readings) remain complete in raw JSON; numeric
  scalar leaves are also normalized. Array points are not currently separate SQL rows.
- Food responses request goals, nutrition and media metadata. Media URL targets are
  not downloaded separately. Fitness Age requests full history lookup. Menstrual
  day views request full responses, baby-movement fields and analysis.
- Body Battery uses the documented default chunking; `--all-data` covers read
  operations, not every alternate parameterization of the same endpoint.
- Original archives are retained whole even when the decoder prefers FIT over GPX/TCX.
- Multisport parent and child records can describe overlapping effort. Their source
  relationships remain in summary JSON; do not blindly sum all rows for total effort.
- Missing files, account permissions and server errors do not become empty success
  checkpoints. A successful empty response only means no data for that request.

## Long backfill

```bash
garmin-sync-backfill start --all-data --refresh-days 0
garmin-sync-backfill status
```

The one-off worker uses the same package as the foreground CLI, resumes checkpoints,
retries incomplete items once, and checks the resulting GeoPackage. It does not
install a recurring scheduler. At a one-second request interval, several years of
daily details plus per-activity workouts can take many hours. The JSON completion
report records the selected mode; retain the private log for dataset-level progress.
