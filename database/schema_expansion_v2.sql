-- Slate - studio and central server
-- Database Schema Expansion v2 (HRMS, Production, IT)

-- ==============================================================
-- 1. HRMS (Human Resources Management System)
-- ==============================================================

CREATE TABLE IF NOT EXISTS leave_requests (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    leave_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    approved_by VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payroll_records (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    pay_period_start DATE NOT NULL,
    pay_period_end DATE NOT NULL,
    base_salary NUMERIC(10, 2),
    overtime_hours NUMERIC(5, 2) DEFAULT 0,
    total_amount NUMERIC(10, 2),
    status VARCHAR(20) DEFAULT 'DRAFT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS onboarding_workflows (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    department VARCHAR(50),
    is_completed BOOLEAN DEFAULT FALSE,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_reviews (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    reviewer_id VARCHAR(255) NOT NULL,
    review_date DATE NOT NULL,
    score NUMERIC(3, 1),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==============================================================
-- 2. PRODUCTION (Scheduling & Bidding)
-- ==============================================================

CREATE TABLE IF NOT EXISTS project_schedules (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    start_date DATE,
    target_date DATE,
    actual_end_date DATE,
    status VARCHAR(50) DEFAULT 'TODO',
    assigned_to VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bidding_costs (
    id SERIAL PRIMARY KEY,
    project_id VARCHAR(255) NOT NULL,
    estimated_hours NUMERIC(10, 2),
    actual_hours NUMERIC(10, 2) DEFAULT 0,
    quoted_price NUMERIC(12, 2),
    currency VARCHAR(10) DEFAULT 'USD',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==============================================================
-- 3. IT & INFRA
-- ==============================================================

CREATE TABLE IF NOT EXISTS hardware_inventory (
    id SERIAL PRIMARY KEY,
    machine_name VARCHAR(100) UNIQUE NOT NULL,
    assigned_to VARCHAR(255),
    gpu_model VARCHAR(100),
    ram_gb INTEGER,
    status VARCHAR(50) DEFAULT 'ACTIVE',
    purchase_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS software_licenses (
    id SERIAL PRIMARY KEY,
    software_name VARCHAR(100) NOT NULL,
    total_seats INTEGER NOT NULL,
    active_seats INTEGER DEFAULT 0,
    expiration_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS it_tickets (
    id SERIAL PRIMARY KEY,
    submitted_by VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    priority VARCHAR(20) DEFAULT 'MEDIUM',
    status VARCHAR(20) DEFAULT 'OPEN',
    resolved_at TIMESTAMP,
    resolved_by VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_leave_requests_user ON leave_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_payroll_records_user ON payroll_records(user_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_workflows_user ON onboarding_workflows(user_id);
CREATE INDEX IF NOT EXISTS idx_project_schedules_project ON project_schedules(project_id);
CREATE INDEX IF NOT EXISTS idx_bidding_costs_project ON bidding_costs(project_id);
CREATE INDEX IF NOT EXISTS idx_it_tickets_status ON it_tickets(status);
