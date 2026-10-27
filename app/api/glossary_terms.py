"""Glossary-term CRUD HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.services import glossary_term_service as service

bp = Blueprint("glossary_terms", __name__)


@bp.post("/projects/<int:project_id>/glossary-terms")
def create_glossary_term(project_id):
    term = service.create(project_id, request.get_json(silent=True) or {})
    return jsonify(term.to_dict()), 201


@bp.get("/projects/<int:project_id>/glossary-terms")
def list_glossary_terms(project_id):
    return jsonify([term.to_dict() for term in service.list_by_project(project_id)])


@bp.get("/glossary-terms/<int:term_id>")
def get_glossary_term(term_id):
    return jsonify(service.get(term_id).to_dict())


@bp.patch("/glossary-terms/<int:term_id>")
def update_glossary_term(term_id):
    return jsonify(service.update(term_id, request.get_json(silent=True) or {}).to_dict())


@bp.delete("/glossary-terms/<int:term_id>")
def delete_glossary_term(term_id):
    return jsonify(service.delete(term_id).to_dict())
