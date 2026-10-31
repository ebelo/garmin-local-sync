"""Garmin HTTP adapter implementing the application's ActivitySource port."""

from ..domain.errors import FailureKind, SourceError
from ..domain.models import ActivityDetail, DateWindow
from .garmin_routes import DETAIL_DATASETS, DETAIL_PARAMS, RANGE_DATASETS
from .http import ApiError
from .mapping import summary_from_payload

FAILURES = {
    401: FailureKind.AUTHENTICATION,
    403: FailureKind.PERMISSION,
    429: FailureKind.RATE_LIMIT,
    404: FailureKind.NOT_FOUND,
    204: FailureKind.NO_CONTENT,
    400: FailureKind.REQUEST_REJECTED,
    413: FailureKind.REQUEST_REJECTED,
    500: FailureKind.TRANSIENT,
    502: FailureKind.TRANSIENT,
    503: FailureKind.TRANSIENT,
    504: FailureKind.TRANSIENT,
}


class GarminSource:
    def __init__(self, transport):
        self.transport = transport

    def _get(self, path, params=None, *, binary=False):
        try:
            if binary:
                return self.transport.get(path, params, binary=True)
            return self.transport.get(path, params) if params is not None else self.transport.get(path)
        except ApiError as error:
            raise SourceError(FAILURES.get(error.status, FailureKind.OTHER), str(error)) from None

    def list_activities(self, offset, limit, include_children=False):
        params = {"start": offset, "limit": limit}
        if include_children:
            params["excludeChildren"] = "false"
        data = self._get("/activitylist-service/activities/search/activities", params)
        if not isinstance(data, list):
            raise RuntimeError("Unexpected activity list response")
        return [summary_from_payload(item) for item in data]

    def activity_detail(self, activity_id):
        data = self._get(f"/activity-service/activity/{activity_id}")
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected activity detail response")
        return ActivityDetail(data, bool(data.get("metadataDTO", {}).get("manualActivity")))

    def download_activity(self, activity_id):
        return self._get(f"/download-service/files/activity/{activity_id}", binary=True)

    def activity_workouts(self, activity_id):
        data = self._get(f"/activity-service/activity/{activity_id}/workouts")
        if not isinstance(data, list):
            raise RuntimeError("Unexpected activity workouts response")
        return data

    def health(self, dataset: str, window: DateWindow):
        a, b = window.start, window.end
        if dataset in RANGE_DATASETS:
            path, mode = RANGE_DATASETS[dataset]
            path = path.format(start=a, end=b)
            params = None
            if mode == "params":
                params = {"startDate": a, "endDate": b}
            elif mode == "weight":
                params = {"includeAll": "true"}
        else:
            path, date_param = DETAIL_DATASETS[dataset]
            path = path.format(day=a)
            params = dict(DETAIL_PARAMS.get(dataset, {}))
            if date_param:
                params[date_param] = a
            params = params or None
        return self._get(path, params)

    def context(self):
        for name, path in (
            ("profile", "/userprofile-service/userprofile/settings"),
            ("heart_rate_zones", "/biometric-service/heartRateZones/v2"),
        ):
            yield name, self._get(path)
