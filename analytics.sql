-- Geometry-free queries; safe to run through sqlite3 or pandas.read_sql_query.
-- Calendar months use Garmin's activity-local date, not a guessed UTC offset.

SELECT substr(start_time_local, 1, 7) AS month, activity_type,
       count(*) AS activities,
       round(sum(distance_km), 1) AS distance_km,
       round(sum(duration_hours), 1) AS hours,
       round(sum(elevation_gain_m)) AS ascent_m
FROM activity_analytics
GROUP BY month, activity_type
ORDER BY month DESC, activity_type;

SELECT calendar_date, steps, resting_hr, sleep_hours, sleep_score,
       hrv_last_night_ms, body_battery_max, stress_average, weight_kg
FROM daily_analytics
ORDER BY calendar_date DESC
LIMIT 30;

-- Training and recovery on the same local date. Missing health data stays NULL.
SELECT date(a.start_time_local) AS day,
       count(*) AS activities, round(sum(a.duration_hours), 2) AS hours,
       round(sum(a.elevation_gain_m)) AS ascent_m,
       max(d.sleep_hours) AS sleep_hours,
       max(d.hrv_last_night_ms) AS hrv_ms,
       max(d.resting_hr) AS resting_hr
FROM activity_analytics a
LEFT JOIN daily_analytics d ON d.calendar_date=date(a.start_time_local)
GROUP BY day
ORDER BY day DESC
LIMIT 30;

-- All decoded samples are accessible without contacting Garmin.
SELECT activity_id, count(*) AS samples, min(timestamp_utc) AS first_utc,
       max(timestamp_utc) AS last_utc, avg(heart_rate_bpm) AS mean_sample_hr
FROM activity_samples
GROUP BY activity_id
ORDER BY activity_id DESC
LIMIT 10;
