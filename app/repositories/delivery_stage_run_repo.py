from app.models.delivery_stage_run import DeliveryStageRun


def create(project_id, **fields):
    return DeliveryStageRun.create(project=project_id, **fields)


def get(run_id):
    return DeliveryStageRun.get_or_none(DeliveryStageRun.id == run_id)


def get_by_number(project_id, number):
    return DeliveryStageRun.get_or_none((DeliveryStageRun.project_id == project_id) & (DeliveryStageRun.run_number == number))


def active(project_id, stage):
    return (DeliveryStageRun.select().where(
        (DeliveryStageRun.project_id == project_id) & (DeliveryStageRun.stage == stage)
        & (DeliveryStageRun.status.in_(("active", "blocked")))
    ).order_by(DeliveryStageRun.id.desc()).first())


def active_any(project_id):
    return (DeliveryStageRun.select().where((DeliveryStageRun.project_id == project_id) & (DeliveryStageRun.status.in_(("active", "blocked")))).first())


def latest_closed(project_id, stage):
    return (DeliveryStageRun.select().where(
        (DeliveryStageRun.project_id == project_id) & (DeliveryStageRun.stage == stage)
        & (DeliveryStageRun.status == "closed")
    ).order_by(DeliveryStageRun.closed_at.desc()).first())


def list_by_project(project_id):
    return list(DeliveryStageRun.select().where(DeliveryStageRun.project_id == project_id).order_by(DeliveryStageRun.id))


def save(run):
    run.save()
    return run
