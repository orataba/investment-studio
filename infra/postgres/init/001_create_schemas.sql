DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portfolio_ops') THEN
    CREATE ROLE portfolio_ops
      LOGIN
      NOCREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOREPLICATION
      NOBYPASSRLS
      PASSWORD 'portfolio_ops';
  ELSE
    ALTER ROLE portfolio_ops WITH
      LOGIN
      NOCREATEDB
      NOSUPERUSER
      NOCREATEROLE
      NOREPLICATION
      NOBYPASSRLS
      PASSWORD 'portfolio_ops';
  END IF;
END
$$;

ALTER DATABASE portfolio_ops OWNER TO portfolio_ops;

CREATE SCHEMA IF NOT EXISTS instrument_registry AUTHORIZATION portfolio_ops;
CREATE SCHEMA IF NOT EXISTS portfolio AUTHORIZATION portfolio_ops;
CREATE SCHEMA IF NOT EXISTS watchlist AUTHORIZATION portfolio_ops;

ALTER SCHEMA instrument_registry OWNER TO portfolio_ops;
ALTER SCHEMA portfolio OWNER TO portfolio_ops;
ALTER SCHEMA watchlist OWNER TO portfolio_ops;
