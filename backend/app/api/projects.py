"""项目管理 API"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.project import Project
from app.models.episode import Episode
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectListResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _project_stats(db: Session, project_id: int) -> dict:
    total = db.query(func.count(Episode.id)).filter(Episode.project_id == project_id).scalar() or 0
    done = (
        db.query(func.count(Episode.id))
        .filter(Episode.project_id == project_id, Episode.status == "done")
        .scalar()
        or 0
    )
    error = (
        db.query(func.count(Episode.id))
        .filter(Episode.project_id == project_id, Episode.status == "error")
        .scalar()
        or 0
    )
    latest = (
        db.query(Episode)
        .filter(Episode.project_id == project_id)
        .order_by(Episode.id.desc())
        .first()
    )
    last_status = latest.status if latest else None
    return {
        "episode_count": total,
        "done_count": done,
        "error_count": error,
        "last_status": last_status,
    }


@router.get("", response_model=ProjectListResponse)
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.updated_at.desc()).all()
    items = []
    for p in projects:
        stats = _project_stats(db, p.id)
        items.append(
            ProjectResponse(
                id=p.id,
                name=p.name,
                mode=p.mode,
                created_at=p.created_at,
                updated_at=p.updated_at,
                episode_count=stats["episode_count"],
                done_count=stats["done_count"],
                error_count=stats["error_count"],
                last_status=stats["last_status"],
            )
        )
    return ProjectListResponse(projects=items, total=len(items))


@router.post("", response_model=ProjectResponse)
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(name=data.name, mode=data.mode)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        mode=project.mode,
        created_at=project.created_at,
        updated_at=project.updated_at,
        episode_count=0,
        done_count=0,
        error_count=0,
        last_status=None,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    stats = _project_stats(db, project.id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        mode=project.mode,
        created_at=project.created_at,
        updated_at=project.updated_at,
        **stats,
    )


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(project_id: int, data: ProjectCreate, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.name = data.name
    project.mode = data.mode
    db.commit()
    db.refresh(project)
    stats = _project_stats(db, project.id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        mode=project.mode,
        created_at=project.created_at,
        updated_at=project.updated_at,
        **stats,
    )


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    import os
    import shutil
    from pathlib import Path
    from app.models.episode import Episode

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    episodes = db.query(Episode).filter(Episode.project_id == project_id).all()
    for ep in episodes:
        if ep.audio_path and os.path.exists(ep.audio_path):
            try:
                os.remove(ep.audio_path)
            except OSError:
                pass
            p = Path(ep.audio_path)
            for extra_name in (f"{p.stem}.mp3", f"{p.stem}_norm.wav"):
                extra = p.with_name(extra_name)
                if extra.exists():
                    try:
                        extra.unlink()
                    except OSError:
                        pass
        seg_dir = Path(ep.audio_path).parent.parent / f"ep_{ep.id}" if ep.audio_path else None
        # 分句目录在 AUDIO_DIR/ep_id
        from app.config import AUDIO_DIR

        seg = AUDIO_DIR / f"ep_{ep.id}"
        if seg.exists():
            shutil.rmtree(seg, ignore_errors=True)

    db.query(Episode).filter(Episode.project_id == project_id).delete()
    db.delete(project)
    db.commit()
    return {"message": "删除成功"}
