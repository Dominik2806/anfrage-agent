-- Schreibende Datenbankrolle des Datenservice hoffmann-data (F08, ADR 0005).
-- Zweck: Der Schreibweg des Datenservice darf Leads, Kontakte und Aktivitäten anlegen, sonst nichts
-- (Auftrag 5.1): nichts ändern, nichts löschen. Die Datenbank setzt das durch, auch wenn der Code einen
-- Fehler hat. Die Rolle hat SELECT und INSERT auf customers, contacts und activities, dazu je Tabelle eine
-- SELECT- und eine INSERT-Policy, weil Row Level Security ohne Policy keine Zeile durchlässt (schema.sql).
-- Kein BYPASSRLS, kein CREATE im Schema, keine Sequenzrechte (Identity-Spalten brauchen keine).
-- Die INSERT-Policies sind streng und setzen ADR 0005 Punkt 7 in der Datenbank durch:
--   customers:  nur status = 'lead'
--   contacts:   ohne Einschränkung
--   activities: nur created_by = 'agent', type inquiry, complaint oder note, ohne Artikelbezug (product_id)
-- products, product_fits und discount_rules sind für diese Rolle unzugänglich.
--
-- Ausführen: Der Mensch führt das Skript in der Datenbank aus (Supabase SQL-Editor oder psql), im
-- Schema der Tabellen (Standard: public). Wiederholbar: Ein zweiter Lauf ändert nichts.
-- NACH JEDEM "python -m db.seed" ERNEUT AUSFÜHREN: Der Reset löscht die Tabellen und mit ihnen
-- Rechte und Policies. Ohne den erneuten Lauf scheitert der Schreibweg beim Start.
-- Die Policies heißen rw_select und rw_insert, die der Leserolle anders: Das Skript der einen Rolle
-- löscht nie eine Policy der anderen.
--
-- Passwort: steht nie in dieser Datei. Der Mensch setzt es separat mit
--   ALTER ROLE <rollenname> PASSWORD '<aus dem Passwortmanager>';
-- und trägt die Verbindung als MCP_SERVER_WRITE_DATABASE_URL in die Umgebung ein (nie ins Repository).
--
-- Die Tests führen dieses Skript unter einem Zufallsnamen aus (exakte Textersetzung des Namens).
-- Deshalb gilt: Der Rollenname steht nur in Anweisungen, nicht in Kommentaren und Policy-Namen,
-- und hier steht kein weiterer Rollenname.

-- Rolle anlegen, falls sie fehlt (ohne Passwort)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'data_service_rw') THEN
        CREATE ROLE data_service_rw;
    END IF;
END
$$;

-- Attribute bei jedem Lauf setzen: kann sich anmelden, sonst nichts
-- Die Option für Administratorrechte wird absichtlich nicht genannt, auch nicht in verneinter Form: Neuere
-- PostgreSQL-Versionen verbieten einem Benutzer ohne Administratorrechte schon die Nennung, und der SQL-Editor von
-- Supabase läuft ohne sie. Eine neue Rolle hat sie ohnehin nicht (Standard von CREATE ROLE), und die Startprüfung
-- des Datenservice lehnt eine Rolle mit Administratorrechten ab.
ALTER ROLE data_service_rw LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Höchstens 5 Sekunden je Abfrage. Die Rolle ist nicht schreibgeschützt: Sie muss schreiben können.
ALTER ROLE data_service_rw SET statement_timeout = '5s';

-- Nutzungsrecht am aktuellen Schema; alle Rechte auf den Tabellen zuerst entziehen
DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO data_service_rw', current_schema());
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM data_service_rw', current_schema());
END
$$;

-- Lesen und anlegen, nur diese drei Tabellen (je Recht eine eigene Anweisung)
GRANT SELECT ON customers, contacts, activities TO data_service_rw;
GRANT INSERT ON customers, contacts, activities TO data_service_rw;

-- customers: sehen darf die Rolle alles, anlegen nur Leads
DROP POLICY IF EXISTS rw_select ON customers;
CREATE POLICY rw_select ON customers FOR SELECT TO data_service_rw USING (true);

DROP POLICY IF EXISTS rw_insert ON customers;
CREATE POLICY rw_insert ON customers FOR INSERT TO data_service_rw WITH CHECK (status = 'lead');

-- contacts: sehen und anlegen ohne Einschränkung
DROP POLICY IF EXISTS rw_select ON contacts;
CREATE POLICY rw_select ON contacts FOR SELECT TO data_service_rw USING (true);

DROP POLICY IF EXISTS rw_insert ON contacts;
CREATE POLICY rw_insert ON contacts FOR INSERT TO data_service_rw WITH CHECK (true);

-- activities: anlegen nur als Agent, nur Anfrage, Reklamation oder Notiz, ohne Artikelbezug
DROP POLICY IF EXISTS rw_select ON activities;
CREATE POLICY rw_select ON activities FOR SELECT TO data_service_rw USING (true);

DROP POLICY IF EXISTS rw_insert ON activities;
CREATE POLICY rw_insert ON activities FOR INSERT TO data_service_rw
    WITH CHECK (
        created_by = 'agent'
        AND type IN ('inquiry', 'complaint', 'note')
        AND product_id IS NULL
    );
