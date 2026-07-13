DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portfolio_ops_test') THEN
    CREATE ROLE portfolio_ops_test
      LOGIN
      CREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOREPLICATION
      NOBYPASSRLS
      PASSWORD 'portfolio_ops_test';
  ELSE
    ALTER ROLE portfolio_ops_test WITH
      LOGIN
      CREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOREPLICATION
      NOBYPASSRLS
      PASSWORD 'portfolio_ops_test';
  END IF;
END
$$;
