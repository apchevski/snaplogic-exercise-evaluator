locals {
  routes = [
    "GET /v1/students",
    "POST /v1/students",
    "GET /v1/students/{slug}",
    "GET /v1/students/{slug}/reports",
    "PATCH /v1/students/{slug}/report",
    "GET /v1/exercises",
    "GET /v1/exercises/{slug}",
    "PUT /v1/exercises/{slug}",
    "GET /v1/exercises/{slug}/resources/{filename}",
    "GET /v1/gradings/{id}",
    "GET /v1/preps/{id}",
    "POST /v1/gradings",
    "POST /v1/preps",
    "POST /v1/exercises",
  ]
}
