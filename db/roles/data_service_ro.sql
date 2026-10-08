-- Lesende Datenbankrolle des Datenservice hoffmann-data (F07).
-- Zweck: Der Datenservice darf nur lesen (Auftrag 5.1). Die Datenbank setzt das durch, auch wenn
-- der Code einen Fehler hat. Die Rolle hat nur SELECT auf fünf Tabellen, dazu je eine SELECT-Policy,
-- weil Row Level Security ohne Policy keine Zeile herausgibt (schema.sql). Kein BYPASSRLS.
-- discount_rules ist bewusst nicht lesbar (kommt mit F09).
--
-- Ausführen: Der Mensch führt das Skript in der Datenbank aus (Supabase SQL-Editor oder psql), im
-- Schema der Tabellen (Standard: public). Wiederholbar: Ein zweiter Lauf ändert nichts.
-- NACH JEDEM "python -m db.seed" ERNEUT AUSFÜHREN: Der Reset löscht die Tabellen und mit ihnen
-- Rechte und Policies. Ohne den erneuten Lauf meldet der Datenservice einen Datenbankfehler.
--
-- Passwort: steht nie in dieser Datei. Der Mensch setzt es separat mit
--   ALTER ROLE <rollenname> PASSWORD '<aus dem Passwortmanager>';
-- und trägt die Verbindung als MCP_SERVER_DATABASE_URL in die Umgebung ein (nie ins Repository).
--
-- Die Tests führen dieses Skript unter einem Zufallsnamen aus (exakte Textersetzung des Namens).
-- Deshalb gilt: Der Rollenname steht nur in Anweisungen, nicht in Kommentaren und Policy-Namen,
-- und hier steht kein weiterer Rollenname. Ändert sich die Zahl der Vorkommen, ist
-- EXPECTED_ROLE_NAME_COUNT in tests/mcp_server/mcp_testkit.py bewusst anzupassen.

-- Rolle anlegen, falls sie fehlt (ohne Passwort)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'data_service_ro') THEN
        CREATE ROLE data_service_ro;
    END IF;
END
$$;

-- Attribute bei jedem Lauf setzen: kann sich anmelden, sonst nichts
ALTER ROLE data_service_ro LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Standardmäßig nur lesende Transaktionen und höchstens 5 Sekunden je Abfrage
ALTER ROLE data_service_ro SET default_transaction_read_only = on;
ALTER ROLE data_service_ro SET statement_timeout = '5s';

-- Nutzungsrecht am aktuellen Schema; alle Rechte auf den Tabellen zuerst entziehen
DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO data_service_ro', current_schema());
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM data_service_ro', current_schema());
END
$$;

-- Nur lesen, nur diese fünf Tabellen
GRANT SELECT ON products, product_fits, customers, contacts, activities TO data_service_ro;

-- Je Tabelle eine SELECT-Policy: Die Rolle sieht alle Zeilen, ändern kann sie keine
DROP POLICY IF EXISTS ro_select ON products;
CREATE POLICY ro_select ON products FOR SELECT TO data_service_ro USING (true);

DROP POLICY IF EXISTS ro_select ON product_fits;
CREATE POLICY ro_select ON product_fits FOR SELECT TO data_service_ro USING (true);

DROP POLICY IF EXISTS ro_select ON customers;
CREATE POLICY ro_select ON customers FOR SELECT TO data_service_ro USING (true);

DROP POLICY IF EXISTS ro_select ON contacts;
CREATE POLICY ro_select ON contacts FOR SELECT TO data_service_ro USING (true);

DROP POLICY IF EXISTS ro_select ON activities;
CREATE POLICY ro_select ON activities FOR SELECT TO data_service_ro USING (true);
