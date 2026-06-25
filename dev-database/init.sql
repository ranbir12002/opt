-- ===================================================================
-- Dev Database Initialization Script
-- ===================================================================
-- This runs automatically on first container start.
-- Creates extensions and sets permissions.
-- ===================================================================

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant all privileges to the dev user
GRANT ALL PRIVILEGES ON DATABASE optificial_dev TO optificial;
GRANT ALL ON SCHEMA public TO optificial;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO optificial;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO optificial;
