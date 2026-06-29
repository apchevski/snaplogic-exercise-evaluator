locals {
  routes = [
    "GET /v1/students",
    "GET /v1/students/{slug}",
    "GET /v1/students/{slug}/reports",
    "GET /v1/exercises",
    "GET /v1/gradings/{id}",
    "GET /v1/preps/{id}",
    "POST /v1/gradings",
    "POST /v1/preps",
  ]
}
