-- 004 — Quản trị dài hạn: danh mục, liên hệ, lớp/nhóm, loại ca tùy chỉnh, quy tắc có hiệu lực theo ngày,
-- lịch sử phiên bản kỳ chốt, hồ sơ ánh xạ đối soát, lưu trữ mềm, quản trị tài khoản qua web, nhật ký sao lưu.

-- ---------- tài khoản ----------
ALTER TABLE app_users ADD COLUMN is_admin boolean NOT NULL DEFAULT false;
ALTER TABLE app_users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE app_users ADD COLUMN last_login_at timestamptz;
UPDATE app_users SET is_admin = true WHERE id = (SELECT id FROM app_users ORDER BY created_at, email LIMIT 1);

-- Tài khoản đầu tiên tự là quản trị.
CREATE OR REPLACE FUNCTION app.first_admin() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM app_users) THEN NEW.is_admin := true; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER users_first_admin BEFORE INSERT ON app_users FOR EACH ROW EXECUTE FUNCTION app.first_admin();

-- Người dùng không tự nâng quyền / đổi email qua UPDATE trực tiếp.
CREATE OR REPLACE FUNCTION app.guard_user_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('app.admin_op', true) = '1' THEN RETURN NEW; END IF;
  IF NEW.is_admin IS DISTINCT FROM OLD.is_admin OR NEW.email IS DISTINCT FROM OLD.email
     OR NEW.is_active IS DISTINCT FROM OLD.is_active THEN
    RAISE EXCEPTION 'ADMIN_ONLY: chỉ quản trị mới được đổi quyền, email hoặc trạng thái tài khoản' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER users_guard BEFORE UPDATE ON app_users FOR EACH ROW EXECUTE FUNCTION app.guard_user_update();

CREATE OR REPLACE FUNCTION app.is_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT EXISTS (SELECT 1 FROM app_users WHERE id = app.uid() AND is_admin AND is_active) $$;

-- Danh sách tài khoản cho trang quản trị (chỉ thông tin tài khoản, KHÔNG có dữ liệu nghiệp vụ).
CREATE OR REPLACE FUNCTION app.admin_list_users()
RETURNS TABLE (id uuid, email text, display_name text, is_active boolean, is_admin boolean, created_at timestamptz,
               last_login_at timestamptz, must_change_password boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  RETURN QUERY SELECT u.id, u.email, u.display_name, u.is_active, u.is_admin, u.created_at, u.last_login_at, u.must_change_password
               FROM app_users u ORDER BY u.created_at;
END $$;

CREATE OR REPLACE FUNCTION app.admin_create_user(p_email text, p_name text, p_hash text, p_rules jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE nid uuid;
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  INSERT INTO app_users(email, display_name, password_hash, must_change_password)
  VALUES (lower(btrim(p_email)), btrim(p_name), p_hash, true) RETURNING id INTO nid;
  INSERT INTO teacher_profiles(user_id, owner_id, display_name) VALUES (nid, nid, btrim(p_name));
  INSERT INTO app_settings(owner_id, timesheet_rules) VALUES (nid, p_rules);
  INSERT INTO timesheet_rule_versions(owner_id, version, valid_from, rules, confirmed, note)
  VALUES (nid, 1, DATE '2000-01-01', p_rules, false, 'Quy tắc đề xuất mặc định');
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (nid, app.uid(), 'app_users', nid::text, 'INSERT', jsonb_build_object('email', lower(btrim(p_email)), 'display_name', p_name),
          'quản trị tạo tài khoản');
  RETURN nid;
END $$;

CREATE OR REPLACE FUNCTION app.admin_set_user(p_id uuid, p_active boolean, p_hash text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  IF p_id = app.uid() AND p_active IS FALSE THEN
    RAISE EXCEPTION 'ADMIN_ONLY: không thể tự khóa tài khoản quản trị của mình' USING ERRCODE = 'P0001';
  END IF;
  PERFORM set_config('app.admin_op', '1', true);
  UPDATE app_users SET is_active = COALESCE(p_active, is_active),
                       password_hash = COALESCE(p_hash, password_hash),
                       must_change_password = CASE WHEN p_hash IS NOT NULL THEN true ELSE must_change_password END
   WHERE id = p_id;
  PERFORM set_config('app.admin_op', '', true);
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (p_id, app.uid(), 'app_users', p_id::text, 'UPDATE',
          jsonb_build_object('is_active', p_active, 'password_reset', p_hash IS NOT NULL), current_setting('app.reason', true));
END $$;

CREATE OR REPLACE FUNCTION app.touch_login(p_id uuid) RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = public, app AS $$ UPDATE app_users SET last_login_at = now() WHERE id = p_id $$;

DROP FUNCTION IF EXISTS app.auth_lookup(text);
CREATE FUNCTION app.auth_lookup(p_email text)
RETURNS TABLE (id uuid, password_hash text, is_active boolean, display_name text, must_change_password boolean, is_admin boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS
$$ SELECT id, password_hash, is_active, display_name, must_change_password, is_admin FROM app_users WHERE email = lower(btrim(p_email)) $$;

-- ---------- lưu trữ mềm ----------
ALTER TABLE students ADD COLUMN archived_at timestamptz;
ALTER TABLE students ADD COLUMN code text;              -- mã học viên ổn định do người dùng đặt (tùy chọn)
ALTER TABLE rate_rules ADD COLUMN archived_at timestamptz;
ALTER TABLE centers ADD COLUMN archived_at timestamptz;
ALTER TABLE locations ADD COLUMN archived_at timestamptz;
ALTER TABLE locations ADD COLUMN address text;
ALTER TABLE centers ADD COLUMN contact text;

-- ---------- danh mục học tập ----------
CREATE TABLE subjects (
    id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name     text NOT NULL CHECK (btrim(name) <> ''),
    notes    text,
    archived_at timestamptz,
    UNIQUE (owner_id, name)
);

CREATE TABLE class_groups (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (btrim(name) <> ''),
    kind          text NOT NULL DEFAULT 'class' CHECK (kind IN ('class', 'group')),   -- lớp học / nhóm học viên
    subject_id    uuid REFERENCES subjects(id) ON DELETE SET NULL,
    location_id   uuid REFERENCES locations(id) ON DELETE SET NULL,
    notes         text,
    archived_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)
);

CREATE TABLE class_group_members (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    group_id   uuid NOT NULL REFERENCES class_groups(id) ON DELETE CASCADE,
    student_id uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    joined_on  date,
    left_on    date,
    CHECK (left_on IS NULL OR joined_on IS NULL OR left_on >= joined_on)
);
CREATE UNIQUE INDEX member_active ON class_group_members (group_id, student_id) WHERE left_on IS NULL;

CREATE TABLE contacts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    student_id uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    name       text NOT NULL CHECK (btrim(name) <> ''),
    relation   text,
    phone      text,
    email      text,
    notes      text,
    is_primary boolean NOT NULL DEFAULT false,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- loại ca tùy chỉnh ----------
-- 4 loại hệ thống (regular/trial/extra/makeup) luôn tồn tại; bảng này lưu tên hiển thị, ẩn/hiện và loại ca mới.
CREATE TABLE session_kinds (
    owner_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    code     text NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{1,29}$'),
    label    text NOT NULL CHECK (btrim(label) <> ''),
    active   boolean NOT NULL DEFAULT true,
    sort     smallint NOT NULL DEFAULT 50,
    builtin  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (owner_id, code)
);
ALTER TABLE schedule_templates DROP CONSTRAINT IF EXISTS schedule_templates_session_kind_check;
ALTER TABLE planned_sessions DROP CONSTRAINT IF EXISTS planned_sessions_session_kind_check;
ALTER TABLE actual_sessions DROP CONSTRAINT IF EXISTS actual_sessions_session_kind_check;

CREATE OR REPLACE FUNCTION app.guard_kind() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
BEGIN
  IF NEW.session_kind IN ('regular', 'trial', 'extra', 'makeup') THEN RETURN NEW; END IF;
  IF NOT EXISTS (SELECT 1 FROM session_kinds k WHERE k.owner_id = NEW.owner_id AND k.code = NEW.session_kind) THEN
    RAISE EXCEPTION 'KIND_UNKNOWN: loại ca "%" chưa được khai báo trong Cài đặt', NEW.session_kind USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tpl_kind BEFORE INSERT OR UPDATE ON schedule_templates FOR EACH ROW EXECUTE FUNCTION app.guard_kind();
CREATE TRIGGER planned_kind BEFORE INSERT OR UPDATE ON planned_sessions FOR EACH ROW EXECUTE FUNCTION app.guard_kind();
CREATE TRIGGER actual_kind BEFORE INSERT OR UPDATE ON actual_sessions FOR EACH ROW EXECUTE FUNCTION app.guard_kind();

ALTER TABLE app_settings ADD COLUMN status_labels jsonb NOT NULL DEFAULT '{}';
ALTER TABLE app_settings ADD COLUMN export_options jsonb NOT NULL DEFAULT '{}';

-- ---------- quy tắc chấm công có hiệu lực theo ngày ----------
CREATE TABLE timesheet_rule_versions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    version    integer NOT NULL,
    valid_from date NOT NULL,
    rules      jsonb NOT NULL,
    confirmed  boolean NOT NULL DEFAULT false,
    confirmed_at timestamptz,
    note       text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES app_users(id),
    UNIQUE (owner_id, version)
);
INSERT INTO timesheet_rule_versions(owner_id, version, valid_from, rules, confirmed, note)
SELECT owner_id, COALESCE((timesheet_rules ->> 'version')::int, 1), DATE '2000-01-01', timesheet_rules,
       COALESCE((timesheet_rules ->> 'confirmed')::boolean, false), 'Chuyển từ cấu hình V1'
FROM app_settings;

-- ---------- lịch sử phiên bản kỳ chốt ----------
CREATE TABLE timesheet_period_versions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    period_id     uuid NOT NULL REFERENCES timesheet_periods(id) ON DELETE CASCADE,
    version       integer NOT NULL,
    locked_at     timestamptz NOT NULL DEFAULT now(),
    locked_by     uuid REFERENCES app_users(id),
    snapshot      jsonb NOT NULL,
    export_sha256 text NOT NULL,
    export_bytes  bytea NOT NULL,
    unlocked_at   timestamptz,
    unlock_reason text,
    UNIQUE (period_id, version)
);
INSERT INTO timesheet_period_versions(owner_id, period_id, version, locked_at, locked_by, snapshot, export_sha256, export_bytes)
SELECT owner_id, id, version, COALESCE(locked_at, now()), locked_by, snapshot, export_sha256, export_bytes
FROM timesheet_periods WHERE snapshot IS NOT NULL AND export_bytes IS NOT NULL;

-- Phiên bản đã chốt là bất biến (chỉ được ghi thời điểm/lý do mở kỳ).
CREATE OR REPLACE FUNCTION app.guard_period_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.export_bytes IS DISTINCT FROM OLD.export_bytes
     OR NEW.export_sha256 IS DISTINCT FROM OLD.export_sha256 OR NEW.version IS DISTINCT FROM OLD.version THEN
    RAISE EXCEPTION 'SNAPSHOT_IMMUTABLE: bản chốt đã lưu không được sửa' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER period_version_guard BEFORE UPDATE ON timesheet_period_versions FOR EACH ROW EXECUTE FUNCTION app.guard_period_version();

-- ---------- hồ sơ ánh xạ file trung tâm ----------
CREATE TABLE recon_mappings (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    center_id  uuid REFERENCES centers(id) ON DELETE SET NULL,
    name       text NOT NULL CHECK (btrim(name) <> ''),
    mapping    jsonb NOT NULL,
    is_default boolean NOT NULL DEFAULT false,
    archived_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)
);

-- ---------- nhật ký sao lưu / khôi phục ----------
CREATE TABLE backup_runs (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    kind       text NOT NULL CHECK (kind IN ('export', 'verify', 'restore')),
    filename   text,
    sha256     text,
    stats      jsonb NOT NULL DEFAULT '{}',
    ok         boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES app_users(id)
);

-- Khôi phục nhật ký cũ từ bản sao lưu (chỉ vào chính tài khoản đang đăng nhập).
CREATE OR REPLACE FUNCTION app.restore_audit(p_rows jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE n integer;
BEGIN
  IF app.uid() IS NULL THEN RAISE EXCEPTION 'OWNER_MISMATCH' USING ERRCODE = 'P0001'; END IF;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, before, after, reason, at)
  SELECT app.uid(), NULL, r ->> 'table_name', r ->> 'row_id', r ->> 'action', r -> 'before', r -> 'after',
         'khôi phục từ sao lưu: ' || COALESCE(r ->> 'reason', ''), (r ->> 'at')::timestamptz
  FROM jsonb_array_elements(p_rows) r;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- ---------- RLS + trigger cho bảng mới ----------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['subjects','class_groups','class_group_members','contacts','session_kinds',
      'timesheet_rule_versions','timesheet_period_versions','recon_mappings','backup_runs']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR SELECT USING (app.can_read(owner_id))', t || '_sel', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR INSERT WITH CHECK (app.can_write(owner_id))', t || '_ins', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR UPDATE USING (app.can_write(owner_id)) WITH CHECK (app.can_write(owner_id))', t || '_upd', t);
    EXECUTE format('CREATE POLICY %I ON %I FOR DELETE USING (app.can_write(owner_id))', t || '_del', t);
    EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION app.audit()', t || '_audit', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION app.guard_owner()', t || '_owner', t);
  END LOOP;
END $$;

-- Con phải cùng chủ sở hữu với cha.
CREATE OR REPLACE FUNCTION app.guard_child_owner() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, app AS $$
BEGIN
  -- IF lồng nhau: PL/pgSQL không đảm bảo AND dừng sớm, NEW.<cột> chỉ được đọc khi đúng bảng.
  IF TG_TABLE_NAME IN ('class_group_members', 'contacts') THEN
    IF NOT EXISTS (SELECT 1 FROM students s WHERE s.id = NEW.student_id AND s.owner_id = NEW.owner_id) THEN
      RAISE EXCEPTION 'OWNER_MISMATCH: hồ sơ học viên thuộc người khác' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'class_group_members' THEN
    IF NOT EXISTS (SELECT 1 FROM class_groups g WHERE g.id = NEW.group_id AND g.owner_id = NEW.owner_id) THEN
      RAISE EXCEPTION 'OWNER_MISMATCH: lớp/nhóm thuộc người khác' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  IF TG_TABLE_NAME = 'timesheet_period_versions' THEN
    IF NOT EXISTS (SELECT 1 FROM timesheet_periods p WHERE p.id = NEW.period_id AND p.owner_id = NEW.owner_id) THEN
      RAISE EXCEPTION 'OWNER_MISMATCH' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER member_owner BEFORE INSERT OR UPDATE ON class_group_members FOR EACH ROW EXECUTE FUNCTION app.guard_child_owner();
CREATE TRIGGER contact_owner BEFORE INSERT OR UPDATE ON contacts FOR EACH ROW EXECUTE FUNCTION app.guard_child_owner();
CREATE TRIGGER pver_owner BEFORE INSERT OR UPDATE ON timesheet_period_versions FOR EACH ROW EXECUTE FUNCTION app.guard_child_owner();

-- Nhật ký cũng ghi thay đổi hồ sơ giáo viên.
CREATE TRIGGER teacher_profiles_audit AFTER INSERT OR UPDATE OR DELETE ON teacher_profiles FOR EACH ROW EXECUTE FUNCTION app.audit();
