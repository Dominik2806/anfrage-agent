-- Schema der Stammdaten (F05), Quelle: docs/DATENMODELL.md
-- Getestet mit PostgreSQL 16 und 17; Mindestversion 15 wegen NULLS NOT DISTINCT.
-- Nur Struktur: keine Daten, kein DROP. Löschen und Neuanlegen übernimmt das Seed-Skript.
-- Kein Volltextindex und kein pg_trgm. Die Suche (F07) rechnet to_tsvector zur Abfragezeit.

-- Produktkatalog
CREATE TABLE products (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Format z. B. AB-1234 oder AB-1234-X1 (Variante)
    article_number text           NOT NULL UNIQUE
        CONSTRAINT products_article_number_format
        CHECK (article_number ~ '^[A-Z]{2}-[0-9]{4}(-[A-Z0-9]{1,4})?$'),
    name           text           NOT NULL,
    category       text           NOT NULL
        CONSTRAINT products_category_check
        CHECK (category IN ('conveyor', 'housing', 'spare_part', 'service_contract')),
    -- enthält Alternativbezeichnungen (z. B. "auch: Gurtförderer")
    description    text           NOT NULL,
    technical_data jsonb          NOT NULL DEFAULT '{}'
        CONSTRAINT products_technical_data_object
        CHECK (jsonb_typeof(technical_data) = 'object'),
    list_price     numeric(10,2)  NOT NULL
        CONSTRAINT products_list_price_check CHECK (list_price >= 0),
    price_unit     text           NOT NULL
        CONSTRAINT products_price_unit_check
        CHECK (price_unit IN ('piece', 'meter', 'year')),
    lead_time_days integer        NOT NULL
        CONSTRAINT products_lead_time_days_check CHECK (lead_time_days >= 0),
    is_active      boolean        NOT NULL DEFAULT true
);

-- Ersatzteil passt zu Anlage (n:m)
CREATE TABLE product_fits (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- das Teil
    product_id      bigint NOT NULL REFERENCES products (id) ON DELETE RESTRICT,
    -- die Anlage
    fits_product_id bigint NOT NULL REFERENCES products (id) ON DELETE RESTRICT,
    note            text,
    CONSTRAINT product_fits_pair_key UNIQUE (product_id, fits_product_id),
    CONSTRAINT product_fits_not_self CHECK (product_id <> fits_product_id)
);

-- Kunden und Leads
CREATE TABLE customers (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_name text        NOT NULL,
    -- Domains enden auf .example (synthetische Daten)
    domain       text        NOT NULL UNIQUE
        CONSTRAINT customers_domain_format
        CHECK (domain = lower(domain)
               AND domain ~ '^[a-z0-9-]+(\.[a-z0-9-]+)*\.example$'),
    industry     text        NOT NULL
        CONSTRAINT customers_industry_check
        CHECK (industry IN ('automotive', 'food', 'packaging', 'metalworking',
                            'plastics', 'logistics', 'other')),
    country      text        NOT NULL
        CONSTRAINT customers_country_check CHECK (country IN ('de', 'at', 'ch')),
    status       text        NOT NULL
        CONSTRAINT customers_status_check
        CHECK (status IN ('existing', 'lead', 'inactive')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Firmenname ist unabhängig von Groß-/Kleinschreibung eindeutig (Dublettenschutz)
CREATE UNIQUE INDEX customers_company_name_lower_key
    ON customers (lower(company_name));

-- Ansprechpartner
CREATE TABLE contacts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint      NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    first_name  text        NOT NULL,
    last_name   text        NOT NULL,
    -- Adressen enden auf .example (synthetische Daten)
    email       text        NOT NULL UNIQUE
        CONSTRAINT contacts_email_format
        CHECK (email = lower(email)
               AND email ~ '^[^@\s]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.example$'),
    job_title   text,
    language    text        NOT NULL
        CONSTRAINT contacts_language_check CHECK (language IN ('de', 'en')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- Ziel für den zusammengesetzten FK aus activities
    CONSTRAINT contacts_id_customer_key UNIQUE (id, customer_id)
);

-- Historie (frühere Anfragen und Aufträge) und neue Protokolleinträge
CREATE TABLE activities (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint        NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    contact_id  bigint,
    product_id  bigint        REFERENCES products (id) ON DELETE RESTRICT,
    type        text          NOT NULL
        CONSTRAINT activities_type_check
        CHECK (type IN ('inquiry', 'quote', 'order', 'complaint', 'service', 'note')),
    occurred_at timestamptz   NOT NULL,
    subject     text          NOT NULL,
    summary     text          NOT NULL,
    amount_eur  numeric(10,2)
        CONSTRAINT activities_amount_eur_check CHECK (amount_eur >= 0),
    created_by  text          NOT NULL
        CONSTRAINT activities_created_by_check
        CHECK (created_by IN ('seed', 'agent', 'staff')),
    -- Der Kontakt muss zum Kunden der Aktivität gehören
    -- (bei contact_id NULL wird der FK nicht geprüft, Standard MATCH SIMPLE)
    CONSTRAINT activities_contact_fk FOREIGN KEY (contact_id, customer_id)
        REFERENCES contacts (id, customer_id) ON DELETE RESTRICT,
    -- Beträge nur bei Angeboten und Aufträgen
    CONSTRAINT activities_amount_type_check
        CHECK (amount_eur IS NULL OR type IN ('quote', 'order'))
);

-- Rabattregeln: max_discount_percent ist die Eskalationsschwelle, keine Zusage
CREATE TABLE discount_rules (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- NULL = alle Kundenstatus
    customer_status      text
        CONSTRAINT discount_rules_customer_status_check
        CHECK (customer_status IN ('existing', 'lead')),
    -- NULL = alle Kategorien
    product_category     text
        CONSTRAINT discount_rules_product_category_check
        CHECK (product_category IN ('conveyor', 'housing', 'spare_part', 'service_contract')),
    min_quantity         integer       NOT NULL DEFAULT 1
        CONSTRAINT discount_rules_min_quantity_check CHECK (min_quantity >= 1),
    max_discount_percent numeric(5,2)  NOT NULL
        CONSTRAINT discount_rules_max_discount_check
        CHECK (max_discount_percent BETWEEN 0 AND 100),
    -- Regeltext auf Deutsch
    description          text          NOT NULL,
    CONSTRAINT discount_rules_scope_key
        UNIQUE NULLS NOT DISTINCT (customer_status, product_category, min_quantity)
);

-- Row Level Security: aktiviert, in diesem Skript bewusst ohne Policies.
-- Über die Supabase-API (anon, authenticated) ist nichts lesbar. Der Datenservice
-- liest über die Rolle data_service_ro (db/roles/data_service_ro.sql). Sie hat nur SELECT
-- auf fünf Tabellen mit je einer SELECT-Policy und kein BYPASSRLS. discount_rules bleibt
-- für sie unlesbar (F09). Nach einem Reset mit python -m db.seed das Rollen-Skript erneut ausführen.
ALTER TABLE products       ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_fits   ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers      ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts       ENABLE ROW LEVEL SECURITY;
ALTER TABLE activities     ENABLE ROW LEVEL SECURITY;
ALTER TABLE discount_rules ENABLE ROW LEVEL SECURITY;
