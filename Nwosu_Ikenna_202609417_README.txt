MIT 8103 ADVANCED DATABASE SYSTEMS - PRACTICAL DATABASE PORTFOLIO
Student    : Ikenna Nwosu
Student ID : 202609417
Programme  : Master of Information Technology, Miva Open University
Session    : 2026/2027 First Semester Continuous Assessment
Case study : Kasuwa Marketplace (simulated Nigerian multi-vendor e-commerce platform)
GitHub     : github.com/<your-username>/advanced-database-portfolio   (private; assessor added as collaborator)

FILES IN THIS SUBMISSION
  Nwosu_Ikenna_202609417_Documentation.docx          2-3 page LMS documentation (Word)
  Nwosu_Ikenna_202609417_Documentation.pdf           same document as PDF
  Nwosu_Ikenna_202609417_Portfolio_Repository.zip    full copy of the GitHub repository
  Nwosu_Ikenna_202609417_README.txt                  this file

REPOSITORY CONTENTS (advanced-database-portfolio/)
  README.md                                  overview, case study, environment, how to run everything
  portfolio-1-database-design/               ERD (dot/png/svg), schema DDL, seed data, constraint tests, normalisation
  portfolio-2-query-optimisation/            5 workload queries, 2 optimisation rounds, benchmark harness, 15 execution plans
  portfolio-3-transactions-concurrency/      COMMIT/ROLLBACK/SAVEPOINT script, 7 concurrency scenarios
  portfolio-4-nosql/                         Redis key-value, JSONB document model, MongoDB-API version
  portfolio-5-distributed-cloud/             4-node replication + sharding lab, 8 experiments, cloud design
  Every portfolio folder has a README.md (what, how tested, results) and an evidence/ folder (raw outputs, screenshots).

SOFTWARE
  PostgreSQL 16, Redis 7, Python 3 (psycopg2-binary, redis, pymongo, mongomock, matplotlib), Graphviz.

QUICK START
  createdb kasuwa
  psql -d kasuwa -f portfolio-1-database-design/01_schema.sql
  psql -d kasuwa -f portfolio-1-database-design/02_seed_data.sql
  then follow the order given in README.md.

AI USE
  Declared in Section 6 of the documentation.
