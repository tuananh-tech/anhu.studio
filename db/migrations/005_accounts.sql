-- 005 — Tài khoản cho bản phát hành: khởi tạo quản trị MỘT lần, lời mời / mã đặt lại một lần (chỉ lưu SHA-256 của mã),
-- phiên đăng nhập thu hồi được, chống dò mật khẩu (lưu trong DB, đúng cho nhiều tiến trình), nhật ký quản trị.
-- Role ứng dụng KHÔNG đọc/ghi trực tiếp các bảng này: mọi thao tác qua hàm SECURITY DEFINER có kiểm tra quyền.

CREATE TABLE app_meta (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- CSDL đã có tài khoản từ trước (nâng cấp) -> coi như đã khởi tạo, không mở lại bước bootstrap.
INSERT INTO app_meta(key, value)
SELECT 'bootstrap_done', jsonb_build_object('at', now(), 'by', 'migration-005-existing-users')
WHERE EXISTS (SELECT 1 FROM app_users);

CREATE TABLE account_invites (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         text NOT NULL CHECK (kind IN ('invite', 'reset')),
    email        text NOT NULL CHECK (email = lower(email)),
    display_name text,
    make_admin   boolean NOT NULL DEFAULT false,
    user_id      uuid REFERENCES app_users(id) ON DELETE CASCADE,
    token_hash   text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at   timestamptz NOT NULL,
    created_by   uuid REFERENCES app_users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    used_at      timestamptz,
    revoked_at   timestamptz
);

CREATE TABLE app_sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    revoked_at   timestamptz,
    client       text
);
CREATE INDEX app_sessions_user ON app_sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE login_attempts (
    id    bigserial PRIMARY KEY,
    email text NOT NULL,
    ok    boolean NOT NULL,
    at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_attempts_email ON login_attempts (email, at);

ALTER TABLE app_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_meta FORCE ROW LEVEL SECURITY;
ALTER TABLE account_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_invites FORCE ROW LEVEL SECURITY;
ALTER TABLE app_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE login_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE login_attempts FORCE ROW LEVEL SECURITY;
-- Không có policy nào: role ứng dụng không thấy dòng nào khi truy vấn trực tiếp.

-- Mã học viên duy nhất trong phạm vi một giáo viên.
CREATE UNIQUE INDEX students_code_unique ON students (owner_id, lower(code)) WHERE code IS NOT NULL;

CREATE OR REPLACE FUNCTION app._new_account(p_email text, p_name text, p_hash text, p_admin boolean, p_rules jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE nid uuid;
BEGIN
  INSERT INTO app_users(email, display_name, password_hash, is_admin, must_change_password)
  VALUES (lower(btrim(p_email)), btrim(p_name), p_hash, p_admin, false) RETURNING id INTO nid;
  INSERT INTO teacher_profiles(user_id, owner_id, display_name) VALUES (nid, nid, btrim(p_name));
  INSERT INTO app_settings(owner_id, timesheet_rules) VALUES (nid, p_rules);
  INSERT INTO timesheet_rule_versions(owner_id, version, valid_from, rules, confirmed, note)
  VALUES (nid, 1, DATE '2000-01-01', p_rules, false, 'Quy tắc đề xuất mặc định');
  RETURN nid;
END $$;
REVOKE ALL ON FUNCTION app._new_account(text, text, text, boolean, jsonb) FROM PUBLIC;

-- ---------- khởi tạo quản trị viên đầu tiên (một lần, vĩnh viễn) ----------
CREATE OR REPLACE FUNCTION app.bootstrap_state() RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT CASE WHEN EXISTS (SELECT 1 FROM app_meta WHERE key = 'bootstrap_done') OR EXISTS (SELECT 1 FROM app_users)
               THEN 'done' ELSE 'open' END $$;

CREATE OR REPLACE FUNCTION app.bootstrap_admin(p_email text, p_name text, p_hash text, p_rules jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE nid uuid;
BEGIN
  -- Khóa: hai yêu cầu khởi tạo đồng thời được xếp hàng; yêu cầu thứ hai thấy 'done' và bị từ chối.
  LOCK TABLE app_meta IN SHARE ROW EXCLUSIVE MODE;
  IF EXISTS (SELECT 1 FROM app_meta WHERE key = 'bootstrap_done') OR EXISTS (SELECT 1 FROM app_users) THEN
    RAISE EXCEPTION 'BOOTSTRAP_DONE: hệ thống đã được khởi tạo — bước này đã bị vô hiệu hóa vĩnh viễn' USING ERRCODE = 'P0001';
  END IF;
  nid := app._new_account(p_email, p_name, p_hash, true, p_rules);
  INSERT INTO app_meta(key, value) VALUES ('bootstrap_done', jsonb_build_object('at', now(), 'user', nid));
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (nid, nid, 'app_users', nid::text, 'INSERT', jsonb_build_object('email', lower(btrim(p_email)), 'is_admin', true),
          'khởi tạo quản trị viên đầu tiên');
  RETURN nid;
END $$;

-- ---------- lời mời & mã đặt lại ----------
CREATE OR REPLACE FUNCTION app.admin_create_invite(p_kind text, p_email text, p_name text, p_admin boolean, p_user uuid,
                                                   p_token_hash text, p_hours integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE iid uuid; em text := lower(btrim(p_email));
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  IF p_hours < 1 OR p_hours > 168 THEN RAISE EXCEPTION 'INVITE_INVALID: thời hạn 1–168 giờ' USING ERRCODE = 'P0001'; END IF;
  IF p_kind = 'invite' THEN
    IF EXISTS (SELECT 1 FROM app_users WHERE email = em) THEN
      RAISE EXCEPTION 'INVITE_INVALID: email này đã có tài khoản' USING ERRCODE = 'P0001';
    END IF;
    IF (SELECT count(*) FROM app_users WHERE is_active) >= 2 THEN
      RAISE EXCEPTION 'MAX_USERS: đã đủ 2 tài khoản hoạt động' USING ERRCODE = 'P0001';
    END IF;
    -- lời mời cũ chưa dùng cho cùng email bị thu hồi
    UPDATE account_invites SET revoked_at = now() WHERE email = em AND kind = 'invite' AND used_at IS NULL AND revoked_at IS NULL;
  ELSE
    SELECT email INTO em FROM app_users WHERE id = p_user;
    IF em IS NULL THEN RAISE EXCEPTION 'INVITE_INVALID: không tìm thấy tài khoản' USING ERRCODE = 'P0001'; END IF;
    UPDATE account_invites SET revoked_at = now() WHERE user_id = p_user AND kind = 'reset' AND used_at IS NULL AND revoked_at IS NULL;
  END IF;
  INSERT INTO account_invites(kind, email, display_name, make_admin, user_id, token_hash, expires_at, created_by)
  VALUES (p_kind, em, btrim(p_name), COALESCE(p_admin, false), p_user, p_token_hash, now() + make_interval(hours => p_hours), app.uid())
  RETURNING id INTO iid;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (app.uid(), app.uid(), 'account_invites', iid::text, 'INSERT',
          jsonb_build_object('kind', p_kind, 'email', em, 'make_admin', COALESCE(p_admin, false), 'hours', p_hours),
          CASE WHEN p_kind = 'invite' THEN 'tạo lời mời tài khoản' ELSE 'tạo mã đặt lại quyền truy cập' END);
  RETURN iid;
END $$;

CREATE OR REPLACE FUNCTION app.admin_list_invites()
RETURNS TABLE (id uuid, kind text, email text, display_name text, make_admin boolean, expires_at timestamptz,
               created_at timestamptz, used_at timestamptz, revoked_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  RETURN QUERY SELECT i.id, i.kind, i.email, i.display_name, i.make_admin, i.expires_at, i.created_at, i.used_at, i.revoked_at
               FROM account_invites i ORDER BY i.created_at DESC LIMIT 50;
END $$;

CREATE OR REPLACE FUNCTION app.admin_revoke_invite(p_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  UPDATE account_invites SET revoked_at = now() WHERE id = p_id AND used_at IS NULL AND revoked_at IS NULL;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, reason)
  VALUES (app.uid(), app.uid(), 'account_invites', p_id::text, 'UPDATE', 'thu hồi lời mời / mã');
END $$;

-- Người được mời (CHƯA đăng nhập) kích hoạt bằng email + mã. Mã chỉ dùng được một lần, có hạn.
CREATE OR REPLACE FUNCTION app.accept_invite(p_email text, p_token_hash text, p_pw_hash text, p_name text, p_rules jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE inv account_invites%ROWTYPE; nid uuid;
BEGIN
  SELECT * INTO inv FROM account_invites WHERE token_hash = p_token_hash FOR UPDATE;
  IF NOT FOUND OR inv.email <> lower(btrim(p_email)) OR inv.used_at IS NOT NULL OR inv.revoked_at IS NOT NULL
     OR inv.expires_at < now() THEN
    RAISE EXCEPTION 'INVITE_INVALID: mã không đúng, đã dùng, đã bị thu hồi hoặc đã hết hạn' USING ERRCODE = 'P0001';
  END IF;
  IF inv.kind = 'invite' THEN
    nid := app._new_account(inv.email, COALESCE(nullif(btrim(p_name), ''), inv.display_name, inv.email), p_pw_hash,
                            inv.make_admin, p_rules);
  ELSE
    nid := inv.user_id;
    PERFORM set_config('app.admin_op', '1', true);
    UPDATE app_users SET password_hash = p_pw_hash, must_change_password = false WHERE id = nid;
    PERFORM set_config('app.admin_op', '', true);
    UPDATE app_sessions SET revoked_at = now() WHERE user_id = nid AND revoked_at IS NULL;
  END IF;
  UPDATE account_invites SET used_at = now(), user_id = nid WHERE id = inv.id;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (nid, nid, 'app_users', nid::text, CASE WHEN inv.kind = 'invite' THEN 'INSERT' ELSE 'UPDATE' END,
          jsonb_build_object('email', inv.email, 'via', inv.kind),
          CASE WHEN inv.kind = 'invite' THEN 'kích hoạt tài khoản từ lời mời' ELSE 'đặt lại mật khẩu bằng mã một lần' END);
  RETURN nid;
END $$;

-- ---------- vai trò ----------
CREATE OR REPLACE FUNCTION app.admin_set_role(p_id uuid, p_admin boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  IF NOT p_admin AND NOT EXISTS (SELECT 1 FROM app_users WHERE is_admin AND is_active AND id <> p_id) THEN
    RAISE EXCEPTION 'ADMIN_ONLY: phải còn ít nhất một quản trị viên hoạt động' USING ERRCODE = 'P0001';
  END IF;
  PERFORM set_config('app.admin_op', '1', true);
  UPDATE app_users SET is_admin = p_admin WHERE id = p_id;
  PERFORM set_config('app.admin_op', '', true);
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (p_id, app.uid(), 'app_users', p_id::text, 'UPDATE', jsonb_build_object('is_admin', p_admin), 'đổi vai trò');
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (app.uid(), app.uid(), 'app_users', p_id::text, 'UPDATE', jsonb_build_object('is_admin', p_admin), 'đổi vai trò (quản trị)');
END $$;

-- Khóa tài khoản cũng thu hồi mọi phiên của tài khoản đó; ghi nhật ký cho cả người thực hiện.
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
  IF p_active IS FALSE OR p_hash IS NOT NULL THEN
    UPDATE app_sessions SET revoked_at = now() WHERE user_id = p_id AND revoked_at IS NULL;
  END IF;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (p_id, app.uid(), 'app_users', p_id::text, 'UPDATE',
          jsonb_build_object('is_active', p_active, 'password_reset', p_hash IS NOT NULL), current_setting('app.reason', true));
  IF p_id <> app.uid() THEN
    INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
    VALUES (app.uid(), app.uid(), 'app_users', p_id::text, 'UPDATE',
            jsonb_build_object('is_active', p_active, 'password_reset', p_hash IS NOT NULL), current_setting('app.reason', true));
  END IF;
END $$;

-- ---------- phiên đăng nhập ----------
-- Gọi sau khi ứng dụng đã kiểm tra mật khẩu. Mã phiên chỉ nằm trong bộ nhớ phía máy chủ (không gửi về trình duyệt).
CREATE OR REPLACE FUNCTION app.session_start(p_user uuid, p_client text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE sid uuid;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM app_users WHERE id = p_user AND is_active) THEN
    RAISE EXCEPTION 'SESSION_INVALID' USING ERRCODE = 'P0001';
  END IF;
  INSERT INTO app_sessions(user_id, client) VALUES (p_user, left(p_client, 200)) RETURNING id INTO sid;
  UPDATE app_users SET last_login_at = now() WHERE id = p_user;
  RETURN sid;
END $$;

-- Kiểm tra phiên mỗi lần giao diện chạy lại: phiên bị thu hồi / tài khoản bị khóa / quá 12 giờ -> hết hiệu lực.
CREATE OR REPLACE FUNCTION app.session_check(p_session uuid)
RETURNS TABLE (user_id uuid, display_name text, is_admin boolean, must_change_password boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  UPDATE app_sessions s SET last_seen_at = now()
   WHERE s.id = p_session AND s.revoked_at IS NULL AND s.last_seen_at < now() - interval '1 minute';
  RETURN QUERY SELECT u.id, u.display_name, u.is_admin, u.must_change_password
    FROM app_sessions s JOIN app_users u ON u.id = s.user_id
   WHERE s.id = p_session AND s.revoked_at IS NULL AND u.is_active
     AND s.created_at > now() - interval '12 hours' AND s.last_seen_at > now() - interval '2 hours';
END $$;

CREATE OR REPLACE FUNCTION app.session_end(p_session uuid) RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = public, app AS $$ UPDATE app_sessions SET revoked_at = now() WHERE id = p_session AND revoked_at IS NULL $$;

-- Người dùng tự đăng xuất mọi thiết bị khác.
CREATE OR REPLACE FUNCTION app.session_revoke_others(p_session uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE n integer;
BEGIN
  UPDATE app_sessions SET revoked_at = now() WHERE user_id = app.uid() AND id <> p_session AND revoked_at IS NULL;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

CREATE OR REPLACE FUNCTION app.admin_revoke_sessions(p_user uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE n integer;
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  UPDATE app_sessions SET revoked_at = now() WHERE user_id = p_user AND revoked_at IS NULL;
  GET DIAGNOSTICS n = ROW_COUNT;
  INSERT INTO audit_log(owner_id, actor_id, table_name, row_id, action, after, reason)
  VALUES (app.uid(), app.uid(), 'app_sessions', p_user::text, 'UPDATE', jsonb_build_object('revoked', n), 'thu hồi phiên đăng nhập');
  RETURN n;
END $$;

CREATE OR REPLACE FUNCTION app.my_sessions()
RETURNS TABLE (id uuid, created_at timestamptz, last_seen_at timestamptz, client text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, app AS $$
  SELECT s.id, s.created_at, s.last_seen_at, s.client FROM app_sessions s
   WHERE s.user_id = app.uid() AND s.revoked_at IS NULL AND s.created_at > now() - interval '12 hours'
   ORDER BY s.last_seen_at DESC $$;

CREATE OR REPLACE FUNCTION app.admin_session_counts()
RETURNS TABLE (user_id uuid, active integer, last_seen timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  RETURN QUERY SELECT s.user_id, count(*)::int, max(s.last_seen_at) FROM app_sessions s
                WHERE s.revoked_at IS NULL AND s.created_at > now() - interval '12 hours' GROUP BY s.user_id;
END $$;

-- ---------- chống dò mật khẩu ----------
CREATE OR REPLACE FUNCTION app.login_allowed(p_email text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, app AS
$$ SELECT count(*) < 8 FROM login_attempts WHERE email = lower(btrim(p_email)) AND NOT ok AND at > now() - interval '15 minutes' $$;

CREATE OR REPLACE FUNCTION app.login_record(p_email text, p_ok boolean) RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = public, app AS $$
  DELETE FROM login_attempts WHERE at < now() - interval '30 days';
  INSERT INTO login_attempts(email, ok) VALUES (left(lower(btrim(p_email)), 200), p_ok);
$$;

-- ---------- nhật ký quản trị (chỉ thông tin tài khoản, không có dữ liệu nghiệp vụ) ----------
CREATE OR REPLACE FUNCTION app.admin_audit(p_limit integer)
RETURNS TABLE (at timestamptz, actor text, table_name text, action text, after jsonb, reason text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, app AS $$
BEGIN
  IF NOT app.is_admin() THEN RAISE EXCEPTION 'ADMIN_ONLY: cần quyền quản trị' USING ERRCODE = 'P0001'; END IF;
  RETURN QUERY SELECT DISTINCT ON (l.at, l.row_id, l.reason) l.at, u.display_name, l.table_name, l.action,
                      l.after - 'password_hash', l.reason
    FROM audit_log l LEFT JOIN app_users u ON u.id = l.actor_id
   WHERE l.table_name IN ('app_users', 'account_invites', 'app_sessions')
   ORDER BY l.at DESC, l.row_id, l.reason LIMIT p_limit;
END $$;
