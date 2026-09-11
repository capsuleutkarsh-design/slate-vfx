from typing import Dict, List


from ...core.domain.review_shot import ShotStatus


class ShotReviewBatchService:
    """Batch/status helpers extracted from ShotReviewTab."""

    STATUS_ICONS: Dict[ShotStatus, str] = {
        ShotStatus.PENDING: "[P]",
        ShotStatus.IN_REVIEW: "[R]",
        ShotStatus.APPROVED: "[OK]",
        ShotStatus.REJECTED: "[X]",
        ShotStatus.RE_REVIEW: "[RR]",
    }

    @classmethod
    def status_icon(cls, status: ShotStatus) -> str:
        return cls.STATUS_ICONS.get(status, "[P]")

    @staticmethod
    def stats_text(shots: List) -> str:
        total = len(shots)
        approved = sum(1 for s in shots if s.status == ShotStatus.APPROVED)
        rejected = sum(1 for s in shots if s.status == ShotStatus.REJECTED)
        pending = total - approved - rejected
        return f"{total} shots | Approved: {approved} | Rejected: {rejected} | Pending: {pending}"

    @staticmethod
    def batch_set_status(shot_items, shot_list, shots, dashboard_sync, status: ShotStatus):
        """
        Apply status to selected QListWidgetItems and sync each shot.
        Returns list of updated shots.
        """
        updated = []
        for item in shot_items:
            row = shot_list.row(item)
            if row < 0 or row >= len(shots):
                continue
            shot = shots[row]
            shot.status = status
            dashboard_sync.sync_shot_status(shot)
            updated.append(shot)
        return updated
