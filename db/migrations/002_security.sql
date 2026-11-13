-- RLS + trigger toàn vẹn. Ứng dụng kết nối bằng role anhu_app (KHÔNG superuser, KHÔNG BYPASSRLS);
-- mỗi transaction đặt app.user_id = id người đăng nhập (SET LOCAL qua set_config(..., true)).

CREATE OR REPLACE FUNCTION app.uid() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION app.can_read(o uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT o = app.uid() OR EXISTS (SELECT 1 FROM data_shares s WHERE s.owner_id = o AND s.grantee_id = app.uid()) $$;

CREATE OR REPLACE FUNCTION app.can_write(o uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT o = app.uid() OR EXISTS (SELECT 1 FROM data_shares s WHERE s.owner_id = o AND s.grantee_id = app.uid() AND s.can_write) $$;

-- Đăng nhập: tra cứu theo email trước khi biết uid (chỉ trả hash, không lộ dữ liệu khác).
CREATE OR REPLACE FUNCTION app.auth_lookup(p_email text)
RETURNS TABLE (id uuid, password_hash text, is_active boolean, display_name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS
$$ SELECT id, password_hash, is_active, display_name FROM app_users WHERE email = lower(btrim(p_email)) $$;

-- Chia sẻ: tìm id tài khoản đang hoạt động theo email (chỉ trả id).
CREATE OR REPLACE FUNCTION app.user_id_by_email(p_email text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS
$$ SELECT id FROM app_users WHERE email = lower(btrim(p_email)) AND is_active $$;

-- ---------------- RLS ----------------
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users FORCE ROW LEVEL SECURITY;
CREATE POLICY users_sel ON app_users FOR SELECT USING (
    id = app.uid() OR EXISTS (SELECT 1 FROM data_shares s
        WHERE (s.owner_id = app_users.id AND s.grantee_id = app.uid()) OR (s.grantee_id = app_users.id AND s.owner_id = app.uid())));
CREATE POLICY users_upd ON app_users FOR UPDATE USING (id = app.uid()) WITH CHECK (id = app.uid());

ALTER TABLE data_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_shares FORCE ROW LEVEL SECURITY;
CREATE POLICY shares_sel ON data_shares FOR SELECT USING (owner_id = app.uid() OR grantee_id = app.uid());
CREATE POLICY shares_ins ON data_shares FOR INSERT WITH CHECK (owner_id = app.uid());
CREATE POLICY shares_upd ON data_shares FOR UPDATE USING (owner_id = app.uid()) WITH CHECK (owner_id = app.uid());
CREATE POLICY shares_del ON data_shares FOR DELETE USING (owner_id = app.uid());

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['teacher_profiles','centers','locations','students','student_enrollments',
      'schedule_templates','planned_sessions','actual_sessions','session_attendees','rate_rules','app_settings',
      'timesheet_periods','legacy_imports','reconciliation_imports','reconciliation_items']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT USING (app.can_read(owner_id))', t || '_sel', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT WITH CHECK (app.can_write(owner_id))', t || '_ins', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE USING (app.can_write(owner_id)) WITH CHECK (app.can_write(owner_id))', t || '_upd', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR DELETE USING (app.can_write(owner_id))', t || '_del', t);
  END LOOP;
END $$;

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_sel ON audit_log FOR SELECT USING (app.can_read(owner_id));
-- không có policy INSERT/UPDATE/DELETE: app không ghi/sửa/xóa nhật ký trực tiếp; chỉ trigger (SECURITY DEFINER).

-- ---------------- Toàn vẹn ----------------
-- Không cho chuyển dữ liệu sang chủ sở hữu khác.
CREATE OR REPLACE FUNCTION app.guard_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
    RAISE EXCEPTION 'OWNER_IMMUTABLE: không được đổi chủ sở hữu dữ liệu' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;

-- Con phải cùng chủ sở hữu với cha (chặn gắn học viên vào ca của người khác).
CREATE OR REPLACE FUNCTION app.guard_attendee_owner() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM actual_sessions a WHERE a.id = NEW.session_id AND a.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'OWNER_MISMATCH: học viên và ca phải cùng chủ sở hữu' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.student_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM students s WHERE s.id = NEW.student_id AND s.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'OWNER_MISMATCH: hồ sơ học viên thuộc người khác' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER attendee_owner BEFORE INSERT OR UPDATE ON session_attendees FOR EACH ROW EXECUTE FUNCTION app.guard_attendee_owner();

CREATE OR REPLACE FUNCTION app.guard_item_owner() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM reconciliation_imports i WHERE i.id = NEW.import_id AND i.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'OWNER_MISMATCH' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER item_owner BEFORE INSERT OR UPDATE ON reconciliation_items FOR EACH ROW EXECUTE FUNCTION app.guard_item_owner();

-- Kỳ đã chốt: không thay đổi âm thầm ca thực tế/học viên trong tháng đó.
CREATE OR REPLACE FUNCTION app.is_locked(o uuid, d date) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT EXISTS (SELECT 1 FROM timesheet_periods p WHERE p.owner_id = o AND p.status = 'locked'
                  AND p.year = EXTRACT(YEAR FROM d) AND p.month = EXTRACT(MONTH FROM d)) $$;

CREATE OR REPLACE FUNCTION app.guard_locked_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') AND app.is_locked(OLD.owner_id, OLD.session_date) THEN
    RAISE EXCEPTION 'PERIOD_LOCKED: kỳ %/% đã chốt — cần mở kỳ (có lý do) trước khi sửa',
      EXTRACT(MONTH FROM OLD.session_date), EXTRACT(YEAR FROM OLD.session_date) USING ERRCODE = 'P0001';
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') AND app.is_locked(NEW.owner_id, NEW.session_date) THEN
    RAISE EXCEPTION 'PERIOD_LOCKED: kỳ %/% đã chốt — cần mở kỳ (có lý do) trước khi sửa',
      EXTRACT(MONTH FROM NEW.session_date), EXTRACT(YEAR FROM NEW.session_date) USING ERRCODE = 'P0001';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER actual_locked BEFORE INSERT OR UPDATE OR DELETE ON actual_sessions FOR EACH ROW EXECUTE FUNCTION app.guard_locked_session();

CREATE OR REPLACE FUNCTION app.guard_locked_attendee() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
DECLARE d date; o uuid;
BEGIN
  SELECT session_date, owner_id INTO d, o FROM actual_sessions WHERE id = COALESCE(NEW.session_id, OLD.session_id);
  IF d IS NOT NULL AND app.is_locked(o, d) THEN
    RAISE EXCEPTION 'PERIOD_LOCKED: kỳ đã chốt — cần mở kỳ trước khi sửa danh sách học viên' USING ERRCODE = 'P0001';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER attendee_locked BEFORE INSERT OR UPDATE OR DELETE ON session_attendees FOR EACH ROW EXECUTE FUNCTION app.guard_locked_attendee();

-- Mở kỳ đã chốt bắt buộc có lý do.
CREATE OR REPLACE FUNCTION app.guard_unlock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'locked' AND NEW.status = 'open' AND coalesce(btrim(current_setting('app.reason', true)), '') = '' THEN
    RAISE EXCEPTION 'UNLOCK_REASON_REQUIRED: mở kỳ đã chốt phải có lý do' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER period_unlock BEFORE UPDATE ON timesheet_periods FOR EACH ROW EXECUTE FUNCTION app.guard_unlock();

-- Tối đa 2 tài khoản hoạt động (whitelist).
CREATE OR REPLACE FUNCTION app.guard_max_users() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT count(*) FROM app_users WHERE is_active) > 2 THEN
    RAISE EXCEPTION 'MAX_USERS: chỉ cho phép 2 tài khoản hoạt động' USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER max_users AFTER INSERT OR UPDATE ON app_users DEFERRABLE INITIALLY IMMEDIATE
  FOR EACH ROW EXECUTE FUNCTION app.guard_max_users();

-- Nhật ký tự động cho mọi thay đổi nghiệp vụ.
CREATE OR REPLACE FUNCTION app.audit() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
DECLARE b jsonb; a jsonb; rid text;
BEGIN
  IF TG_OP <> 'INSERT' THEN b := to_jsonb(OLD) - 'file_bytes' - 'export_bytes' - 'password_hash'; END IF;
  IF TG_OP <> 'DELETE' THEN a := to_jsonb(NEW) - 'file_bytes' - 'export_bytes' - 'password_hash'; END IF;
  rid := COALESCE(a ->> 'id', b ->> 'id', a ->> 'user_id', b ->> 'user_id', a ->> 'owner_id', b ->> 'owner_id');
  IF TG_OP = 'UPDATE' AND a = b THEN RETURN NULL; END IF;
  INSERT INTO audit_log (owner_id, actor_id, table_name, row_id, action, before, after, reason)
  VALUES (COALESCE((a ->> 'owner_id')::uuid, (b ->> 'owner_id')::uuid), app.uid(), TG_TABLE_NAME, rid, TG_OP, b, a,
          nullif(current_setting('app.reason', true), ''));
  RETURN NULL;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['centers','locations','students','student_enrollments','schedule_templates','planned_sessions',
      'actual_sessions','session_attendees','rate_rules','app_settings','timesheet_periods','legacy_imports',
      'reconciliation_imports','reconciliation_items','data_shares']
  LOOP
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION app.audit()', t || '_audit', t);
    IF t <> 'data_shares' THEN
      EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION app.guard_owner()', t || '_owner', t);
    END IF;
  END LOOP;
END $$;

-- audit cho data_shares: owner_id có sẵn. updated_at tự cập nhật:
CREATE OR REPLACE FUNCTION app.touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
CREATE TRIGGER planned_touch BEFORE UPDATE ON planned_sessions FOR EACH ROW EXECUTE FUNCTION app.touch();
CREATE TRIGGER actual_touch BEFORE UPDATE ON actual_sessions FOR EACH ROW EXECUTE FUNCTION app.touch();
