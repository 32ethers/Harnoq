"""HTTP blueprints."""
from .agent_templates import bp as agent_templates_bp
from .auth import bp as auth_bp
from .users import bp as users_bp
from .agent_tasks import bp as agent_tasks_bp
from .change_requests import bp as change_requests_bp
from .delivery_items import bp as delivery_items_bp
from .delivery_stage_runs import bp as delivery_stage_runs_bp
from .design_cases import bp as design_cases_bp
from .development_tasks import bp as development_tasks_bp
from .features import bp as features_bp
from .glossary_terms import bp as glossary_terms_bp
from .health import bp as health_bp
from .project_constitutions import bp as project_constitutions_bp
from .projects import bp as projects_bp
from .source_requirements import bp as source_requirements_bp
from .skills import bp as skills_bp
from ..web import bp as web_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(source_requirements_bp)
    app.register_blueprint(change_requests_bp)
    app.register_blueprint(delivery_items_bp)
    app.register_blueprint(delivery_stage_runs_bp)
    app.register_blueprint(features_bp)
    app.register_blueprint(glossary_terms_bp)
    app.register_blueprint(project_constitutions_bp)
    app.register_blueprint(design_cases_bp)
    app.register_blueprint(development_tasks_bp)
    app.register_blueprint(skills_bp)
    app.register_blueprint(agent_templates_bp)
    app.register_blueprint(agent_tasks_bp)
    app.register_blueprint(web_bp)
