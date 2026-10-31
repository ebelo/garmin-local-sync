"""Supported Personal API datasets and request-window sizes."""

RANGE_DATASETS = {
    "daily": ("/usersummary-service/v2/dailysummaries", "params"),
    "sleep": ("/sleep-service/sleep/dailySleepSummariesByDate", "params"),
    "hrv": ("/hrv-service/hrv/daily/{start}/{end}", "path"),
    "body_battery": ("/wellness-service/wellness/bodyBattery/reports/daily", "params"),
    "steps": ("/usersummary-service/stats/steps/daily/{start}/{end}", "path"),
    "stress": ("/usersummary-service/stats/stress/daily/{start}/{end}", "path"),
    "hydration": ("/usersummary-service/stats/hydration/daily/{start}/{end}", "path"),
    "weight": ("/weight-service/weight/range/{start}/{end}", "weight"),
    "weight_goals": ("/goal-service/goal/user/effective/weightgoal/{start}/{end}", "path"),
    "wellness_activities": ("/wellnessactivity-service/activity/summary/range/{start}/{end}", "path"),
}


DETAIL_DATASETS = {
    "heart_rate": ("/wellness-service/wellness/dailyHeartRate", "date"),
    "sleep_detail": ("/sleep-service/sleep/dailySleep", "date"),
    "hrv_detail": ("/hrv-service/hrv/{day}", None),
    "spo2": ("/wellness-service/wellness/daily/spo2acclimation/{day}", None),
    "food_logs": ("/nutrition-service/food/logs/{day}", None),
    "fitness_age": ("/fitnessage-service/fitnessage/v2/{day}", None),
}


RANGE_DAYS = {"daily": 183, "hrv": 365, "weight": 365, "weight_goals": 365, "wellness_activities": 365}
