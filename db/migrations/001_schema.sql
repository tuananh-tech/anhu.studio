-- ANHU Teaching Studio V1 — schema (PostgreSQL 15+/Supabase)
-- Mọi bảng nghiệp vụ có owner_id: dữ liệu thuộc về một tài khoản giáo viên; RLS ở 002_rls.sql.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE app_users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE CHECK (email = lower(email)),
    display_name  text NOT NULL,
    password_hash text NOT NULL,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE teacher_profiles (
    user_id      uuid PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    owner_id     uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    display_name text NOT NULL,
    subject      text,
    CHECK (owner_id = user_id)
);

-- Chia sẻ có chủ đích: owner cho grantee xem (và tùy chọn sửa) dữ liệu của mình.
CREATE TABLE data_shares (
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    grantee_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    can_write  boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, grantee_id),
    CHECK (owner_id <> grantee_id)
);

CREATE TABLE centers (
    id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    name     text NOT NULL,
    notes    text,
    active   boolean NOT NULL DEFAULT true,
    UNIQUE (owner_id, name)
);

CREATE TABLE locations (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id  uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    center_id uuid REFERENCES centers(id) ON DELETE SET NULL,
    name      text NOT NULL,          -- giá trị cột B 'CƠ SỞ'
    kind      text NOT NULL DEFAULT 'center_branch' CHECK (kind IN ('center_branch', 'private_place')),
    active    boolean NOT NULL DEFAULT true,
    UNIQUE (owner_id, name)
);

CREATE TABLE students (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id     uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    display_name text NOT NULL,
    search_name  text NOT NULL,       -- không dấu, chữ thường
    class_group  text,
    subject      text,
    start_date   date,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'stopped')),
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX students_search ON students (owner_id, search_name);

-- Một học viên có thể học nhiều trung tâm/lớp và cả dạy riêng (nhiều-nhiều, theo ID).
CREATE TABLE student_enrollments (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    student_id    uuid NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    teaching_type text NOT NULL CHECK (teaching_type IN ('center', 'private')),
    center_id     uuid REFERENCES centers(id) ON DELETE SET NULL,
    location_id   uuid REFERENCES locations(id) ON DELETE SET NULL,
    class_group   text
);

-- Lịch lặp có phiên bản: đổi cả chuỗi = đóng phiên bản cũ (valid_to) + tạo phiên bản mới cùng series_id.
CREATE TABLE schedule_templates (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    series_id     uuid NOT NULL DEFAULT gen_random_uuid(),
    weekday       smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),   -- 0 = thứ Hai
    start_time    time NOT NULL,
    end_time      time NOT NULL,
    tz            text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    valid_from    date NOT NULL,
    valid_to      date,
    teaching_type text NOT NULL CHECK (teaching_type IN ('center', 'private')),
    center_id     uuid REFERENCES centers(id) ON DELETE SET NULL,
    location_id   uuid REFERENCES locations(id) ON DELETE SET NULL,
    class_group   text,
    session_kind  text NOT NULL DEFAULT 'regular' CHECK (session_kind IN ('regular', 'trial', 'extra', 'makeup')),
    expected_students jsonb NOT NULL DEFAULT '[]',   -- [{student_id, name}] theo thứ tự, không khử trùng
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (end_time > start_time),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE planned_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    template_id     uuid REFERENCES schedule_templates(id) ON DELETE SET NULL,
    series_id       uuid,
    occurrence_date date,                 -- ngày gốc theo lịch lặp (giữ khi dời/sửa riêng ca)
    session_date    date NOT NULL,
    start_time      time NOT NULL,
    end_time        time NOT NULL,
    teaching_type   text NOT NULL CHECK (teaching_type IN ('center', 'private')),
    center_id       uuid REFERENCES centers(id) ON DELETE SET NULL,
    location_id     uuid REFERENCES locations(id) ON DELETE SET NULL,
    class_group     text,
    session_kind    text NOT NULL DEFAULT 'regular' CHECK (session_kind IN ('regular', 'trial', 'extra', 'makeup')),
    status          text NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'confirmed', 'cancelled', 'rescheduled', 'needs_review')),
    is_exception    boolean NOT NULL DEFAULT false,   -- đã sửa riêng ca này -> không bị sinh lại đè
    cancel_reason   text CHECK (cancel_reason IN ('teacher_off', 'student_off', 'center_cancel', 'other')),
    cancel_note     text,
    rescheduled_from_id uuid REFERENCES planned_sessions(id) ON DELETE SET NULL,
    makeup_for_id   uuid REFERENCES planned_sessions(id) ON DELETE SET NULL,
    expected_students jsonb NOT NULL DEFAULT '[]',
    notes           text,
    conflict_override_reason text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (end_time > start_time),
    CHECK (status <> 'cancelled' OR cancel_reason IS NOT NULL)
);
CREATE UNIQUE INDEX planned_series_occ ON planned_sessions (series_id, occurrence_date) WHERE series_id IS NOT NULL;
CREATE UNIQUE INDEX planned_one_makeup ON planned_sessions (makeup_for_id) WHERE makeup_for_id IS NOT NULL;
CREATE UNIQUE INDEX planned_one_resched ON planned_sessions (rescheduled_from_id) WHERE rescheduled_from_id IS NOT NULL;
CREATE INDEX planned_by_date ON planned_sessions (owner_id, session_date);

-- Nguồn sự thật của chấm công: actual_sessions đã xác nhận + session_attendees.
CREATE TABLE actual_sessions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id           uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    planned_session_id uuid UNIQUE REFERENCES planned_sessions(id) ON DELETE SET NULL,
    session_date       date NOT NULL,
    start_time         time,
    end_time           time,
    duration_min       integer GENERATED ALWAYS AS
                       ((EXTRACT(EPOCH FROM (end_time - start_time)) / 60)::integer) STORED,
    teaching_type      text NOT NULL CHECK (teaching_type IN ('center', 'private')),
    center_id          uuid REFERENCES centers(id) ON DELETE SET NULL,
    location_id        uuid REFERENCES locations(id) ON DELETE SET NULL,
    location_label     text NOT NULL DEFAULT '',
    class_group        text,
    session_kind       text NOT NULL DEFAULT 'regular' CHECK (session_kind IN ('regular', 'trial', 'extra', 'makeup')),
    status             text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'needs_review', 'void')),
    review_reason      text,
    source             text NOT NULL CHECK (source IN ('schedule', 'manual', 'import_legacy')),
    source_fingerprint text,
    legacy_import_id   uuid,
    makeup_for_planned_id uuid REFERENCES planned_sessions(id) ON DELETE SET NULL,
    notes              text,
    price_amount       numeric(14, 2),             -- dạy riêng: chỉ khi giá đã cấu hình/xác nhận
    payment_status     text NOT NULL DEFAULT 'na' CHECK (payment_status IN ('na', 'unpaid', 'partial', 'paid')),
    paid_amount        numeric(14, 2) NOT NULL DEFAULT 0,
    conflict_override_reason text,
    confirmed_at       timestamptz,
    confirmed_by       uuid REFERENCES app_users(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (start_time IS NULL OR end_time IS NULL OR end_time > start_time),
    CHECK (status <> 'confirmed' OR (start_time IS NOT NULL AND end_time IS NOT NULL))
);
CREATE UNIQUE INDEX actual_fp ON actual_sessions (owner_id, source_fingerprint) WHERE source_fingerprint IS NOT NULL;
CREATE UNIQUE INDEX actual_one_makeup ON actual_sessions (makeup_for_planned_id) WHERE makeup_for_planned_id IS NOT NULL AND status <> 'void';
CREATE INDEX actual_by_date ON actual_sessions (owner_id, session_date);

CREATE TABLE session_attendees (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    session_id uuid NOT NULL REFERENCES actual_sessions(id) ON DELETE CASCADE,
    position   smallint NOT NULL,
    student_id uuid REFERENCES students(id) ON DELETE SET NULL,
    name       text NOT NULL CHECK (btrim(name) <> ''),
    verified   boolean NOT NULL DEFAULT false,      -- dữ liệu cũ chỉ có tên: chờ xác minh
    UNIQUE (session_id, position)
);

CREATE TABLE rate_rules (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    teaching_type text NOT NULL CHECK (teaching_type IN ('center', 'private')),
    center_id     uuid REFERENCES centers(id) ON DELETE CASCADE,
    student_id    uuid REFERENCES students(id) ON DELETE CASCADE,    -- giá buổi dạy riêng theo học viên
    category      text NOT NULL CHECK (category IN ('hv1','hv2','hv3','hv4','hv5plus','min30','trial_extra','private_session')),
    amount        numeric(14, 2) NOT NULL CHECK (amount >= 0),
    valid_from    date NOT NULL,
    valid_to      date,
    confirmed     boolean NOT NULL DEFAULT false,
    confirmed_at  timestamptz,
    note          text,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- Quy tắc xếp cột/hủy ca/ý nghĩa 'Còn không' — có phiên bản, phải xác nhận.
CREATE TABLE app_settings (
    owner_id              uuid PRIMARY KEY REFERENCES app_users(id) ON DELETE CASCADE,
    timesheet_rules       jsonb NOT NULL DEFAULT '{}',
    cancel_pay_rules      jsonb NOT NULL DEFAULT '{}',   -- {reason: {"pay": bool, "confirmed": bool}}
    flag_meaning          text,                          -- ý nghĩa cột 'Còn không' do người dùng xác nhận
    flag_meaning_confirmed boolean NOT NULL DEFAULT false,
    updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE timesheet_periods (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    year          smallint NOT NULL,
    month         smallint NOT NULL CHECK (month BETWEEN 1 AND 12),
    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'locked')),
    locked_at     timestamptz,
    locked_by     uuid REFERENCES app_users(id),
    snapshot      jsonb,
    export_sha256 text,
    export_bytes  bytea,
    version       integer NOT NULL DEFAULT 0,
    last_unlock_reason text,
    UNIQUE (owner_id, year, month)
);

CREATE TABLE legacy_imports (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    filename    text NOT NULL,
    file_sha256 text NOT NULL,
    file_bytes  bytea NOT NULL,
    stats       jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'preview' CHECK (status IN ('preview', 'applied', 'discarded')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    applied_at  timestamptz
);

CREATE TABLE reconciliation_imports (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    center_id   uuid REFERENCES centers(id) ON DELETE SET NULL,
    year        smallint NOT NULL,
    month       smallint NOT NULL,
    filename    text NOT NULL,
    file_sha256 text NOT NULL,
    file_bytes  bytea NOT NULL,           -- file gốc của trung tâm, không bao giờ sửa
    mapping     jsonb,
    status      text NOT NULL DEFAULT 'uploaded' CHECK (status IN ('uploaded', 'compared', 'closed')),
    compared_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reconciliation_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    uuid NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    import_id   uuid NOT NULL REFERENCES reconciliation_imports(id) ON DELETE CASCADE,
    day         date NOT NULL,
    category    text NOT NULL,
    detail      text NOT NULL,
    our         jsonb NOT NULL DEFAULT '{}',
    their       jsonb NOT NULL DEFAULT '{}',
    amount_diff numeric(14, 2),
    resolution  text NOT NULL DEFAULT 'pending'
                CHECK (resolution IN ('pending', 'accept_ours', 'accept_theirs', 'ignored', 'auto_match')),
    resolution_note text,
    resolved_at timestamptz,
    resolved_by uuid REFERENCES app_users(id)
);

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    owner_id   uuid,
    actor_id   uuid,
    table_name text NOT NULL,
    row_id     text,
    action     text NOT NULL,
    before     jsonb,
    after      jsonb,
    reason     text,
    at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_by_row ON audit_log (table_name, row_id);
