-- Maritime Emergency Transfer DB schema (MySQL 8.x)
-- Charset/Collation: utf8mb4 (한글 + 이모지 안전)

CREATE DATABASE IF NOT EXISTS maritime_transfer
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE maritime_transfer;


-- ============================================================
-- 1. cases : 환자 사건 (모든 데이터의 루트)
-- ============================================================
CREATE TABLE cases (
    case_id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    created_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                  ON UPDATE CURRENT_TIMESTAMP(3),
    ship_name         VARCHAR(120)    NULL,
    ship_lat          DECIMAL(9,6)    NULL,
    ship_lon          DECIMAL(9,6)    NULL,
    ship_nx           SMALLINT        NULL,
    ship_ny           SMALLINT        NULL,
    trauma_flag       TINYINT(1)      NOT NULL DEFAULT 0,
    patient_status    ENUM('EVALUATING','RECOMMENDED','TRANSPORTED','CLOSED','CANCELLED')
                                       NOT NULL DEFAULT 'EVALUATING',
    note              VARCHAR(500)    NULL,
    PRIMARY KEY (case_id),
    KEY idx_cases_created_at (created_at),
    KEY idx_cases_status     (patient_status)
) ENGINE=InnoDB;


-- ============================================================
-- 2. vital_inputs : 입력된 vital sign (case 1 : 1)
-- ============================================================
CREATE TABLE vital_inputs (
    case_id          BIGINT UNSIGNED NOT NULL,
    rr               SMALLINT        NOT NULL,
    spo2             SMALLINT        NOT NULL,
    oxygen           TINYINT(1)      NOT NULL,
    sbp              SMALLINT        NOT NULL,
    hr               SMALLINT        NOT NULL,
    consciousness    ENUM('A','C','V','P','U') NOT NULL,
    temp             DECIMAL(4,1)    NOT NULL,
    spo2_scale       TINYINT         NOT NULL DEFAULT 1,
    PRIMARY KEY (case_id),
    CONSTRAINT fk_vital_case
        FOREIGN KEY (case_id) REFERENCES cases(case_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- ============================================================
-- 3. news2_results : 환자평가v2 산출물 (case 1 : 1)
-- ============================================================
CREATE TABLE news2_results (
    case_id                  BIGINT UNSIGNED NOT NULL,
    total_score              SMALLINT NOT NULL,
    risk_level               ENUM('NORMAL','LOW','SINGLE_RED','MEDIUM','HIGH') NOT NULL,
    single_red               TINYINT(1) NOT NULL,
    red_items                JSON NOT NULL,
    item_scores              JSON NOT NULL,
    required_hospital_type   ENUM('EMERGENCY_MEDICAL_INSTITUTION','TRAUMA_CENTER') NOT NULL,
    required_resources       JSON NOT NULL,
    PRIMARY KEY (case_id),
    KEY idx_news2_risk (risk_level),
    CONSTRAINT fk_news2_case
        FOREIGN KEY (case_id) REFERENCES cases(case_id)
        ON DELETE CASCADE
) ENGINE=InnoDB;


-- ============================================================
-- 4a. hospitals_master : 병원 마스터 (영구 보관, 월 단위 갱신)
--     hpid를 PK로 하는 정적 정보 (위치/이름/외상센터 여부 등)
-- ============================================================
CREATE TABLE hospitals_master (
    hpid                  VARCHAR(20)  NOT NULL,
    phpid                 VARCHAR(20)  NULL,
    duty_name             VARCHAR(200) NOT NULL,
    duty_addr             VARCHAR(300) NULL,
    duty_tel              VARCHAR(50)  NULL,
    duty_tel3             VARCHAR(50)  NULL,
    duty_emcls            VARCHAR(10)  NULL,
    duty_emcls_name       VARCHAR(80)  NULL,
    lat                   DECIMAL(9,6) NULL,
    lon                   DECIMAL(9,6) NULL,
    nx                    SMALLINT     NULL,    -- 기상청 격자
    ny                    SMALLINT     NULL,
    is_trauma_center      TINYINT(1)   NOT NULL DEFAULT 0,
    region_sido           VARCHAR(40)  NULL,
    region_sigungu        VARCHAR(60)  NULL,
    is_active             TINYINT(1)   NOT NULL DEFAULT 1,
    source_collected_at   DATETIME     NULL,
    created_at            DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                       ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (hpid),
    KEY idx_master_phpid  (phpid),
    KEY idx_master_emcls  (duty_emcls),
    KEY idx_master_trauma (is_trauma_center),
    KEY idx_master_region (region_sido, region_sigungu)
) ENGINE=InnoDB;


-- ============================================================
-- 4b. hospital_snapshot : 응급의료 API 결과 캐시 (TTL: 5~10분)
--     실시간 가용 자원만 저장. 정적 정보는 hospitals_master 조인.
--     - 같은 hpid는 snapshot_at 마다 row가 늘어남(append-only)
-- ============================================================
CREATE TABLE hospital_snapshot (
    hpid                  VARCHAR(20)  NOT NULL,
    snapshot_at           DATETIME(3)  NOT NULL,
    hvec                  INT NULL,    -- 응급실 가용 병상
    hvoc                  INT NULL,    -- 수술실
    hvicc                 INT NULL,    -- 일반 중환자실
    hv31                  INT NULL,
    hv34                  INT NULL,
    hvcc                  INT NULL,
    hv6                   INT NULL,
    hv9                   INT NULL,
    hv39                  INT NULL,
    hv60                  INT NULL,
    hv61                  INT NULL,
    hvctayn               CHAR(1) NULL,    -- CT
    hvangioayn            CHAR(1) NULL,    -- 혈관조영
    hv7                   VARCHAR(10) NULL,
    hvventiayn            CHAR(1) NULL,    -- 인공호흡기
    mkiosk_ty1            CHAR(1) NULL,    -- 심근경색 수용
    mkiosk_ty2            CHAR(1) NULL,
    mkiosk_ty3            CHAR(1) NULL,
    mkiosk_ty4            CHAR(1) NULL,
    mkiosk_ty5            CHAR(1) NULL,
    mkiosk_ty6            CHAR(1) NULL,
    mkiosk_ty11           CHAR(1) NULL,
    mkiosk_ty19           CHAR(1) NULL,
    mkiosk_ty22           CHAR(1) NULL,
    mkiosk_ty23           CHAR(1) NULL,
    raw_json              JSON NULL,
    PRIMARY KEY (hpid, snapshot_at),
    KEY idx_hospital_snapshot_time (snapshot_at),
    CONSTRAINT fk_snapshot_master
        FOREIGN KEY (hpid) REFERENCES hospitals_master(hpid)
        ON DELETE RESTRICT
) ENGINE=InnoDB;


-- ============================================================
-- 5. weather_snapshot : 기상청 초단기실황 캐시 (1시간 단위)
-- ============================================================
CREATE TABLE weather_snapshot (
    nx               SMALLINT     NOT NULL,
    ny               SMALLINT     NOT NULL,
    base_datetime    DATETIME     NOT NULL,    -- base_date + base_time 합친 값
    pty              TINYINT      NULL,
    rn1              DECIMAL(6,2) NULL,
    wsd              DECIMAL(5,2) NULL,
    reh              DECIMAL(5,2) NULL,
    t1h              DECIMAL(5,2) NULL,
    raw_json         JSON         NULL,
    fetched_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (nx, ny, base_datetime)
) ENGINE=InnoDB;


-- ============================================================
-- 6. recommendations : 케이스별 후보 + 정렬용 컬럼
--    - rank는 저장하지 않고 ORDER BY로 동적 계산 (가중치 실험 자유)
--    - 필요해지면 rank 컬럼 추가 가능
-- ============================================================
CREATE TABLE recommendations (
    case_id                       BIGINT UNSIGNED NOT NULL,
    hpid                          VARCHAR(20)     NOT NULL,
    hospital_snapshot_at          DATETIME(3)     NULL,
    weather_base_datetime         DATETIME        NULL,

    -- 거리/비행
    distance_m                    INT             NOT NULL,
    distance_nm                   DECIMAL(6,2)    NOT NULL,
    flight_time_min               DECIMAL(6,2)    NOT NULL,
    distance_level                ENUM('GO','CAUTION','NO_GO') NOT NULL,
    distance_no_go                TINYINT(1)      NOT NULL,
    is_fallback_distance_candidate TINYINT(1)     NOT NULL,

    -- 기상
    origin_weather_score          SMALLINT        NOT NULL DEFAULT 0,
    target_weather_score          SMALLINT        NOT NULL DEFAULT 0,
    route_weather_score_max       SMALLINT        NOT NULL DEFAULT 0,
    route_weather_score_avg       DECIMAL(5,2)    NOT NULL DEFAULT 0,
    final_weather_score           SMALLINT        NOT NULL,
    weather_no_go                 TINYINT(1)      NOT NULL,
    flight_level                  ENUM('GO','CAUTION','NO_GO') NOT NULL,

    -- 정렬용 lexicographic 키 (낮을수록 우선)
    recommendation_type           ENUM('NORMAL','FALLBACK') NOT NULL,
    recommendation_type_rank      TINYINT         NOT NULL,    -- NORMAL=0, FALLBACK=1
    patient_risk_rank             TINYINT         NOT NULL,    -- HIGH=0 .. NORMAL=4
    trauma_center_rank            TINYINT         NOT NULL,    -- trauma 환자 대상 0/1
    flight_level_rank             TINYINT         NOT NULL,    -- GO=0 CAUTION=1
    distance_level_rank           TINYINT         NOT NULL,
    tier                          TINYINT         NOT NULL,    -- T1~T6 (1~6)

    -- 환자 자원 적합성
    can_fly                       TINYINT(1)      NOT NULL,
    decision_reason               VARCHAR(500)    NULL,
    sort_key_json                 JSON            NULL,

    created_at                    DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    PRIMARY KEY (case_id, hpid),
    KEY idx_recom_sort
        (case_id, recommendation_type_rank, patient_risk_rank,
         trauma_center_rank, flight_level_rank, distance_level_rank,
         flight_time_min),
    CONSTRAINT fk_recom_case
        FOREIGN KEY (case_id) REFERENCES cases(case_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_recom_snapshot
        FOREIGN KEY (hpid, hospital_snapshot_at)
        REFERENCES hospital_snapshot(hpid, snapshot_at)
        ON DELETE RESTRICT
) ENGINE=InnoDB;


-- ============================================================
-- 7. (선택) v_recommendations_ranked : 정렬된 뷰
--    웹UI는 이 뷰 + WHERE case_id=? LIMIT N 으로 조회
-- ============================================================
CREATE OR REPLACE VIEW v_recommendations_ranked AS
SELECT
    r.*,
    m.duty_name,
    m.lat              AS hospital_lat,
    m.lon              AS hospital_lon,
    m.is_trauma_center,
    s.hvec, s.hvoc, s.hvicc, s.hv31,
    s.hvventiayn, s.hvangioayn, s.hvctayn, s.mkiosk_ty1
FROM recommendations r
JOIN hospitals_master m
  ON m.hpid = r.hpid
LEFT JOIN hospital_snapshot s
  ON s.hpid = r.hpid
 AND s.snapshot_at = r.hospital_snapshot_at
ORDER BY
    r.case_id,
    r.recommendation_type_rank,
    r.patient_risk_rank,
    r.trauma_center_rank,
    r.flight_level_rank,
    r.distance_level_rank,
    r.flight_time_min,
    r.final_weather_score,
    r.distance_m;
