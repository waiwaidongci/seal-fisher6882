from src.services import studies,runs
# Controllers keep endpoint contracts separate from persistence and calculation.
def overview(db): return {'studies':studies.list_studies(db),'runs':runs.list_runs(db),'batches':runs.batches(db)}
def create_study(db,data,user): return studies.create(db,data,user)
def create_joint(db,data,user): return studies.create_joint(db,data,user)
def update_members(db,id,data,user): return studies.update_members(db,id,data,user)
def dependency(db,id): return studies.dependency_view(db,id)
def list_rows(db,id): return studies.observations(db,id)
def save_row(db,id,data,user,row_id=None): return studies.save_observation(db,id,data,user,row_id)
def delete_row(db,id,row_id,user): return studies.delete_observation(db,id,row_id,user)
def create_run(db,data,user): return runs.create(db,data,user)
def review_run(db,id,data,user): return runs.approve(db,id,data,user)
def batches(db): return runs.batches(db)
