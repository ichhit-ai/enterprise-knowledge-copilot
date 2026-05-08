#!/usr/bin/env python3
"""Generate expanded tickets CSV: fix malformed rows, add columns, add 150 new tickets."""
import csv, random, os
from datetime import datetime, timedelta

random.seed(42)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BACKUP_PATH = os.path.join(DATA_DIR, "nexacorp_tickets_original.csv")
OUT_PATH = os.path.join(DATA_DIR, "nexacorp_tickets.csv")
# Use backup if it exists, otherwise the script expects original 100-row CSV
IN_PATH = BACKUP_PATH if os.path.exists(BACKUP_PATH) else OUT_PATH

# Priority mapping from org chart severity data
SEVERITY_MAP = {
    "ERR-AUTH-9092": "P2", "ERR-AUTH-8801": "P2", "ERR-AUTH-0041": "P3",
    "DB-TIMEOUT-404X": "P3", "DB-LOCK-1192X": "P2", "DB-CONN-POOL-99": "P1",
    "VPN-CERT-7731": "P3", "VPN-HANDSHAKE-003": "P2", "VPN-DNS-1882": "P2",
    "SYNC-FAIL-2201B": "P2", "SYNC-DELTA-441C": "P3", "SYNC-HASH-773B": "P3",
    "MAIL-RELAY-550X": "P2", "MAIL-QUEUE-8823": "P2", "MAIL-SPF-4401": "P3",
    "BUILD-PIPE-ERR-88": "P2", "BUILD-DEP-ERR-21": "P3", "BUILD-DOCKER-55X": "P2",
    "FW-DROP-3341": "P1", "FW-RULE-9910": "P2", "FW-IDS-2209": "P1",
    "HR-SESSION-992": "P3", "HR-LDAP-552": "P2", "HR-SYNC-8812": "P2",
    "MONITOR-ALERT-119": "P3", "MONITOR-DISK-88A": "P2",
    "BACKUP-CHKSUM-77Z": "P2", "BACKUP-ROTATE-33": "P2",
    "API-RATE-4429": "P3", "API-TIMEOUT-7720": "P2",
}

RESOLUTION_TEMPLATES = {
    "ERR-AUTH-9092": "Re-provisioned MFA token in HashiCorp Vault. User re-enrolled YubiKey successfully.",
    "ERR-AUTH-8801": "Restored certificate bundle from backup. Restarted AUTH-GATEWAY nginx. Verified health check.",
    "ERR-AUTH-0041": "Updated AUTH-GATEWAY LDAP schema mapping to match new AD schema. Forced full LDAP sync.",
    "DB-TIMEOUT-404X": "Optimized query plan by adding composite index on ORDERS(customer_id, created_at). Query now completes in 12 seconds.",
    "DB-LOCK-1192X": "Identified and resolved deadlock by reordering transaction locks. Added row-level locking hint.",
    "DB-CONN-POOL-99": "Increased connection pool size from 100 to 200. Added connection timeout of 30s. Killed stale connections.",
    "VPN-CERT-7731": "Re-issued VPN client certificate via MDM push. User confirmed connectivity restored.",
    "VPN-HANDSHAKE-003": "Upgraded NEXAVPN client from 3.2 to 3.4.1 to support TLS 1.2. Handshake now succeeds.",
    "VPN-DNS-1882": "Corrected DNS split-tunnel configuration for *.nexacorp.internal domain. Flushed DNS cache.",
    "SYNC-FAIL-2201B": "Refreshed AWS IAM role credentials for CLOUDSYNC-S3 service account. Sync resumed.",
    "SYNC-DELTA-441C": "Rebuilt manifest file from S3 inventory report. Delta computation now running correctly.",
    "SYNC-HASH-773B": "Identified NAS storage array error causing bit flips. Replaced faulty DIMM. Re-synced files.",
    "MAIL-RELAY-550X": "Added new relay server IPs to DNS SPF record. Contacted blacklist providers for delisting.",
    "MAIL-QUEUE-8823": "Brought NEXAMAIL relay node 2 back online. Queue drained within 45 minutes.",
    "MAIL-SPF-4401": "Updated SPF TXT record in DNS to include third-party marketing platform IP range.",
    "BUILD-PIPE-ERR-88": "Fixed SonarQube quality gate by increasing unit test coverage to 82%. Build passed.",
    "BUILD-DEP-ERR-21": "Updated internal npm registry mirror. Resolved deprecated package conflicts.",
    "BUILD-DOCKER-55X": "Migrated base image from Python 3.9 alpine to Python 3.12 slim. All CVEs resolved.",
    "FW-DROP-3341": "Confirmed authorized data transfer. Added temporary DLP exception for compliance export.",
    "FW-RULE-9910": "Removed 14 orphaned firewall rules from decommissioned systems. Rule evaluation order restored.",
    "FW-IDS-2209": "Quarantined affected host. Forensic investigation confirmed false positive from authorized pen-test tool.",
    "HR-SESSION-992": "Patched HRPORTAL session manager memory leak. Increased session timeout to configured 30 minutes.",
    "HR-LDAP-552": "Fixed LDAP group misconfiguration for new employee batch. All 6 users can now access HRPORTAL.",
    "HR-SYNC-8812": "Added graceful backoff logic to Workday sync job. Cleared auto-generated P3 tickets from TICKETSYS.",
    "MONITOR-ALERT-119": "Refreshed PagerDuty API token. Alert pipeline restored. CPU spike was caused by batch job.",
    "MONITOR-DISK-88A": "Purged old mail spool files. Implemented log rotation policy. Disk usage reduced to 62%.",
    "BACKUP-CHKSUM-77Z": "Replaced faulty storage array module. Re-ran backup with verified checksums. Restore drill passed.",
    "BACKUP-ROTATE-33": "Vendor repaired tape library robot arm. Offsite rotation completed successfully.",
    "API-RATE-4429": "Corrected rate limit policy file. Restored premium tier consumers to 5000 req/min.",
    "API-TIMEOUT-7720": "Synchronized clocks between API consumer and AUTH-GATEWAY. JWT validation latency resolved.",
}

# Regular employee names for new tickets
REG_EMPLOYEES = [
    "Aisha Patel", "Ben Torres", "Clara Johansson", "David Okonkwo", "Elena Vasquez",
    "Faisal Ahmed", "Grace Liu", "Hassan Mahmoud", "Iris Chen", "Jake Morrison",
    "Kira Nakamura", "Leo Fernandez", "Maya Singh", "Noah Williams", "Olga Petrov",
    "Pablo Gutierrez", "Quinn O'Brien", "Rosa Martinez", "Sam Adeyemi", "Tina Park",
    "Umar Diallo", "Vera Johansson", "Wei Zhang", "Xena Brooks", "Yuki Tanaka",
]

NEW_TICKET_TEMPLATES = [
    ("Can't log into HRPORTAL after password reset. Getting authentication error on login page.", "In Progress", "HR-LDAP-552", "P3"),
    ("VPN drops every 20 minutes during video calls. Have to reconnect each time. Very disruptive.", "In Progress", "VPN-CERT-7731", "P3"),
    ("Getting ERR-AUTH-9092 after trying to set up MFA on my new YubiKey. Can't access any system.", "Resolved", "ERR-AUTH-9092", "P2"),
    ("Email from client bounced back with SPF error. Urgent deal communication blocked.", "Resolved", "MAIL-RELAY-550X", "P3"),
    ("HRPORTAL logs me out after 2 minutes. Can't complete my performance review.", "In Progress", "HR-SESSION-992", "P3"),
    ("New hire - Day 1 and I have no access to any NexaCorp system. Need onboarding help.", "Resolved", "ERR-AUTH-0041", "P2"),
    ("VPN certificate expired. Can't work from home. Downloaded new client but still failing.", "Resolved", "VPN-CERT-7731", "P3"),
    ("Submitted expense report on HRPORTAL but it disappeared after session timeout.", "Resolved", "HR-SESSION-992", "P4"),
    ("Can't connect to VPN from hotel WiFi during business trip. TLS error.", "In Progress", "VPN-HANDSHAKE-003", "P3"),
    ("Internal wiki pages not loading over VPN. DNS resolution failing for *.nexacorp.internal.", "Resolved", "VPN-DNS-1882", "P3"),
    ("MFA prompt appears but YubiKey not recognized after firmware update.", "Resolved", "ERR-AUTH-9092", "P2"),
    ("Need access to NEXACORE-DB reporting schema for quarterly analytics.", "Resolved", "ERR-AUTH-0041", "P4"),
    ("Laptop can't connect to office WiFi after Windows update. Certificate error.", "In Progress", "VPN-HANDSHAKE-003", "P3"),
    ("Getting locked out after 3 failed login attempts. MFA code timing out.", "Resolved", "ERR-AUTH-9092", "P3"),
    ("HRPORTAL leave balance showing data from last month. Not updated.", "In Progress", "HR-SYNC-8812", "P3"),
    ("Received suspicious email claiming to be from IT Security. Possible phishing.", "Escalated", "FW-IDS-2209", "P2"),
    ("VPN connection established but extremely slow. Ping times over 500ms.", "In Progress", "VPN-DNS-1882", "P3"),
    ("Can't download software from IT Downloads page. Getting 403 forbidden.", "Resolved", "ERR-AUTH-0041", "P4"),
    ("My Duo Mobile push notifications stopped working for MFA.", "Resolved", "ERR-AUTH-9092", "P3"),
    ("Team shared drive not syncing to cloud. Files uploaded 3 days ago still not visible.", "In Progress", "SYNC-FAIL-2201B", "P3"),
    ("Accidentally sent confidential file to personal email. DLP alert triggered.", "Escalated", "FW-DROP-3341", "P1"),
    ("Jenkins build failing for my branch. Dependency conflict with internal npm package.", "Resolved", "BUILD-DEP-ERR-21", "P3"),
    ("Need VPN access for contractor starting Monday. How do I request?", "Resolved", "VPN-CERT-7731", "P4"),
    ("HRPORTAL performance review page crashing when I try to submit self-assessment.", "In Progress", "HR-SESSION-992", "P3"),
    ("API rate limit error when testing our integration. Getting HTTP 429.", "Resolved", "API-RATE-4429", "P3"),
    ("Workstation flagged by security scan. Was running authorized Nessus test.", "Resolved", "FW-DROP-3341", "P3"),
    ("Can't access Grafana dashboards over VPN. Connection times out.", "In Progress", "VPN-DNS-1882", "P3"),
    ("Email delivery delayed by 3 hours. Client complaining about response time.", "Resolved", "MAIL-QUEUE-8823", "P3"),
    ("MFA enrollment link expired before I could complete setup. Need new link.", "Resolved", "ERR-AUTH-9092", "P3"),
    ("Docker build failing with critical CVE in base image. Can't push to registry.", "In Progress", "BUILD-DOCKER-55X", "P2"),
    ("Lost VPN connection during database migration. Need to reconnect urgently.", "Resolved", "VPN-CERT-7731", "P2"),
    ("HRPORTAL showing wrong manager in my profile. Can't get leave approved.", "In Progress", "HR-SYNC-8812", "P4"),
    ("Getting certificate mismatch error on all internal sites since this morning.", "Escalated", "ERR-AUTH-8801", "P2"),
    ("Backup verification email says my team's project files failed checksum.", "In Progress", "BACKUP-CHKSUM-77Z", "P3"),
    ("Can't log in to TICKETSYS to file a ticket about not being able to log in.", "Resolved", "ERR-AUTH-0041", "P3"),
    ("VPN disconnects when switching from WiFi to ethernet. Have to restart client.", "In Progress", "VPN-HANDSHAKE-003", "P4"),
    ("Need emergency access to production database for critical bug fix.", "Escalated", "DB-CONN-POOL-99", "P1"),
    ("My API key was accidentally committed to a public repo. Need rotation ASAP.", "Escalated", "FW-IDS-2209", "P1"),
    ("HRPORTAL payslip download giving 500 error for last 3 months.", "Resolved", "HR-LDAP-552", "P3"),
    ("Cloud sync showing hash mismatch for my team's design files.", "In Progress", "SYNC-HASH-773B", "P3"),
    ("Requested MacBook Pro 2 weeks ago through HRPORTAL. No update on status.", "In Progress", "HR-SYNC-8812", "P4"),
    ("Browser showing SSL certificate error for auth.internal.nexacorp.com.", "Resolved", "ERR-AUTH-8801", "P2"),
    ("VPN works but internal DNS not resolving after office network change.", "Resolved", "VPN-DNS-1882", "P3"),
    ("Pipeline blocked - SonarQube quality gate failing on test coverage.", "Resolved", "BUILD-PIPE-ERR-88", "P3"),
    ("Getting HTTP 502 from API gateway when calling /v2/payments endpoint.", "In Progress", "API-TIMEOUT-7720", "P2"),
    ("Multiple team members locked out of VPN simultaneously. Branch office issue.", "Escalated", "VPN-HANDSHAKE-003", "P1"),
    ("HRPORTAL benefits enrollment page not loading during open enrollment period.", "Escalated", "HR-SESSION-992", "P2"),
    ("Suspicious login attempt notification from AUTH-GATEWAY. Wasn't me.", "Escalated", "FW-IDS-2209", "P1"),
    ("Email attachment blocked by DLP filter. Need to send contract to external counsel.", "Resolved", "FW-DROP-3341", "P2"),
    ("Database query running forever on reporting dashboard. Can't generate Q4 report.", "In Progress", "DB-TIMEOUT-404X", "P3"),
    ("New contractor needs temporary VPN access for 30-day engagement.", "Resolved", "VPN-CERT-7731", "P4"),
    ("My MFA device was lost/stolen. Need emergency access revocation and new device.", "Escalated", "ERR-AUTH-9092", "P1"),
    ("Cloud backup failed three nights in a row. Getting BACKUP-CHKSUM-77Z alerts.", "Escalated", "BACKUP-CHKSUM-77Z", "P2"),
    ("Can't push Docker image to internal registry. Trivy scan blocking on CVE.", "In Progress", "BUILD-DOCKER-55X", "P3"),
    ("HRPORTAL showing 'Account Not Found' error despite being an active employee.", "Resolved", "ERR-AUTH-0041", "P2"),
    ("VPN extremely slow from Singapore office after network changes.", "In Progress", "VPN-DNS-1882", "P2"),
    ("Need to whitelist external IP for partner API integration testing.", "Resolved", "FW-RULE-9910", "P4"),
    ("Received MONITOR-DISK-88A alert for my team's dev server. Disk at 91%.", "Resolved", "MONITOR-DISK-88A", "P3"),
    ("Application connection pool errors during peak hours. Users seeing timeouts.", "In Progress", "DB-CONN-POOL-99", "P2"),
    ("Email from nexacorp.com domain being rejected by Gmail. SPF issue.", "Resolved", "MAIL-SPF-4401", "P3"),
    ("Can't complete security awareness training on HRPORTAL. Video not loading.", "In Progress", "HR-SESSION-992", "P4"),
    ("Build pipeline stuck at integration test stage for 2 hours.", "Resolved", "BUILD-PIPE-ERR-88", "P3"),
    ("Firewall blocking my connection to staging environment after rule change.", "Resolved", "FW-RULE-9910", "P3"),
    ("Mail queue alert - outbound emails delayed by 2+ hours company-wide.", "Escalated", "MAIL-QUEUE-8823", "P2"),
    ("S3 sync job failing since AWS credential rotation last night.", "In Progress", "SYNC-FAIL-2201B", "P2"),
    ("Deadlock detected on database during end-of-month payroll processing.", "Escalated", "DB-LOCK-1192X", "P1"),
    ("JWT token validation failing for mobile app users. 401 errors.", "In Progress", "API-TIMEOUT-7720", "P2"),
    ("HRPORTAL Workday sync failed. My updated address not reflected in system.", "Resolved", "HR-SYNC-8812", "P4"),
    ("Need emergency firewall rule for DR failover test this weekend.", "Resolved", "FW-RULE-9910", "P3"),
    ("YubiKey 5 not working with Chrome browser. Firefox works fine.", "Resolved", "ERR-AUTH-9092", "P4"),
    ("Database connection pool exhausted during ETL batch job window.", "In Progress", "DB-CONN-POOL-99", "P2"),
    ("Tape rotation job failed again. Third time this month.", "Escalated", "BACKUP-ROTATE-33", "P2"),
    ("Monitoring alert threshold too sensitive. Getting false alarms every hour.", "Resolved", "MONITOR-ALERT-119", "P4"),
    ("Can't access developer portal. API documentation returning 404.", "In Progress", "API-RATE-4429", "P3"),
    ("PagerDuty not receiving alerts from MONITORX. Integration broken.", "Resolved", "MONITOR-ALERT-119", "P2"),
    ("S3 delta manifest corrupted during concurrent file writes from HRPORTAL export.", "In Progress", "SYNC-DELTA-441C", "P3"),
    ("Multiple build pipelines stuck waiting for Jenkins agent. Agents disconnecting.", "In Progress", "BUILD-PIPE-ERR-88", "P2"),
    ("Need RBAC role upgrade from READ_ONLY to READ_WRITE for project migration.", "Resolved", "DB-TIMEOUT-404X", "P4"),
    ("LDAP group policy cascade failure after emergency offboarding.", "Resolved", "HR-LDAP-552", "P2"),
    ("Bulk VPN certificate renewal missed my subnet. 12 users affected.", "In Progress", "VPN-CERT-7731", "P2"),
    ("Auth-Gateway health check failing after NTP drift on server.", "Resolved", "ERR-AUTH-8801", "P2"),
    ("Data exfiltration DLP alert - was actually moving files to approved backup.", "Resolved", "FW-DROP-3341", "P3"),
    ("Incremental backup failed due to redo log archive destination full.", "Escalated", "BACKUP-CHKSUM-77Z", "P2"),
    ("Cloud cost tagging audit found 23 untagged resources. Need cleanup.", "Resolved", "SYNC-FAIL-2201B", "P4"),
    ("NEXAVPN packet loss exceeding 5% on primary uplink for 30 minutes.", "In Progress", "MONITOR-ALERT-119", "P2"),
    ("HRPORTAL self-assessment form data lost after unexpected session timeout.", "In Progress", "HR-SESSION-992", "P3"),
    ("API consumer reporting intermittent 429 errors despite premium tier.", "Resolved", "API-RATE-4429", "P3"),
    ("Internal DNS failing for all VPN users at Chicago branch office.", "Escalated", "VPN-DNS-1882", "P1"),
    ("Build gate enforcing 80% coverage but our new module has no tests yet.", "In Progress", "BUILD-PIPE-ERR-88", "P3"),
    ("Cross-schema join query timing out on reporting dashboard.", "Resolved", "DB-TIMEOUT-404X", "P3"),
    ("CLOUDSYNC-S3 hash verification failure on legal contracts directory.", "Escalated", "SYNC-HASH-773B", "P2"),
    ("Rate limiter configuration rolled back after accidental downgrade.", "Resolved", "API-RATE-4429", "P3"),
    ("NEXAMAIL server disk at 94%. Mail spool needs purging urgently.", "Resolved", "MONITOR-DISK-88A", "P2"),
    ("Firewall rule conflict detected after maintenance window changes.", "Resolved", "FW-RULE-9910", "P3"),
    ("Connection pool spike during end-of-month reporting. 200 threads queued.", "Escalated", "DB-CONN-POOL-99", "P1"),
    ("AWS IAM permission lost after quarterly policy tightening by security.", "Resolved", "SYNC-FAIL-2201B", "P3"),
    ("Session manager memory leak causing mass logout from HRPORTAL.", "Escalated", "HR-SESSION-992", "P2"),
    ("Brute force pattern detected against login endpoint. Need investigation.", "Escalated", "FW-IDS-2209", "P1"),
    ("Workday down for maintenance but sync job keeps retrying and flooding tickets.", "Resolved", "HR-SYNC-8812", "P3"),
    ("Password rotation script failed for service accounts. Manual rotation needed.", "Resolved", "ERR-AUTH-0041", "P2"),
    ("Cannot complete quarterly restore drill. Backup tapes unavailable.", "In Progress", "BACKUP-ROTATE-33", "P2"),
    ("VPN client 3.2 incompatible after TLS 1.1 deprecation. Need upgrade.", "Resolved", "VPN-HANDSHAKE-003", "P3"),
    ("Snort IDS alert for lateral movement between dev hosts. Investigating.", "Escalated", "FW-IDS-2209", "P1"),
    ("Developer portal showing outdated API docs. External partners confused.", "Resolved", "API-RATE-4429", "P4"),
    ("LDAP sync delay causing new hires to wait 4+ hours for system access.", "In Progress", "ERR-AUTH-0041", "P2"),
    ("Database standby node fell behind during switchover test. Aborting DR drill.", "In Progress", "DB-TIMEOUT-404X", "P2"),
    ("Compliance evidence S3 bucket permission error during SOC2 audit prep.", "Resolved", "SYNC-FAIL-2201B", "P2"),
    ("Build dependency resolver conflict between Python 3.9 and 3.12 packages.", "Resolved", "BUILD-DEP-ERR-21", "P3"),
    ("MFA batch re-provisioning needed after YubiKey firmware update for 15 users.", "Resolved", "ERR-AUTH-9092", "P2"),
    ("Monitoring false positive - CPU spike was scheduled batch processing.", "Resolved", "MONITOR-ALERT-119", "P4"),
    ("NEXABACKUP cold storage node at 89% capacity. Need retention review.", "In Progress", "MONITOR-DISK-88A", "P3"),
    ("VPN MDM certificate push failed for all iOS devices. 28 users affected.", "In Progress", "VPN-CERT-7731", "P2"),
    ("All OAuth2 clients failing after AUTH-GATEWAY key rotation.", "Escalated", "API-TIMEOUT-7720", "P1"),
    ("Emergency offboarding - departing employee LDAP removal caused cascade failure.", "Resolved", "HR-LDAP-552", "P2"),
    ("Orphaned firewall rules from decommissioned systems found during audit.", "Resolved", "FW-RULE-9910", "P3"),
    ("Spam campaign spoofing nexacorp.com flooding inbound queue with bouncebacks.", "Resolved", "MAIL-QUEUE-8823", "P2"),
    ("Cloud manifest inconsistency during DR failover test. Rebuilding.", "In Progress", "SYNC-DELTA-441C", "P3"),
    ("Query plan regression after index rebuild. Full table scan on 400M rows.", "Resolved", "DB-TIMEOUT-404X", "P2"),
    ("Internal npm registry mirror stale. Missing packages from last 10 days.", "Resolved", "BUILD-DEP-ERR-21", "P3"),
    ("Annual backup verification failed on 2 of 8 tape sets. Scheduling restore drill.", "In Progress", "BACKUP-CHKSUM-77Z", "P2"),
    ("Three-way deadlock between invoicing, inventory, and audit transactions.", "In Progress", "DB-LOCK-1192X", "P2"),
    ("BUILDPIPE-CI executor nodes all showing CPU saturation above 95%.", "In Progress", "MONITOR-ALERT-119", "P2"),
    ("HRPORTAL database backup checksum mismatch. Personnel records table.", "Escalated", "BACKUP-CHKSUM-77Z", "P1"),
    ("JWT signing key mismatch after certificate rotation. All tokens rejected.", "Resolved", "API-TIMEOUT-7720", "P2"),
    ("Temporary firewall exception requested for authorized compliance data export.", "Resolved", "FW-DROP-3341", "P3"),
    ("Clock skew between API gateway and auth server causing token validation failures.", "Resolved", "API-TIMEOUT-7720", "P2"),
    ("Kernel patch crashed mail relay node. Queue depth hit 15000 messages.", "Resolved", "MAIL-QUEUE-8823", "P2"),
    ("Concurrent ETL loads exhausting connection pool on reporting replica.", "In Progress", "DB-CONN-POOL-99", "P2"),
    ("Marketing email platform IPs not in SPF record after infrastructure migration.", "Resolved", "MAIL-SPF-4401", "P3"),
    ("Command-and-control beacon pattern alert from developer workstation. Quarantined.", "Escalated", "FW-IDS-2209", "P1"),
    ("BGP route update causing VPN routing issues for Singapore office.", "Resolved", "VPN-DNS-1882", "P2"),
    ("Tape library robot arm fault. Intermittent rotation failures. Vendor contacted.", "Escalated", "BACKUP-ROTATE-33", "P2"),
    ("End-of-month full backup set failed rotation to offsite vault.", "In Progress", "BACKUP-ROTATE-33", "P2"),
    ("Pen-test tool triggered DLP alert on developer workstation. False positive.", "Resolved", "FW-DROP-3341", "P3"),
    ("Q4 salary data sync failed between Workday and HRPORTAL. Payslips impacted.", "Escalated", "HR-SYNC-8812", "P2"),
    ("macOS Sonoma update broke OpenSSL compatibility with VPN client.", "In Progress", "VPN-HANDSHAKE-003", "P3"),
]

def rand_ts(year=2024):
    start = datetime(year, 1, 1)
    delta = timedelta(days=random.randint(0, 364), hours=random.randint(6, 20),
                      minutes=random.randint(0, 59))
    return (start + delta).strftime("%Y-%m-%d %H:%M")

def main():
    # Read existing tickets
    existing = []
    with open(IN_PATH, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Fix: join list values from malformed rows
            clean = {}
            for k, v in row.items():
                if v is None:
                    continue
                clean[k] = " ".join(v) if isinstance(v, list) else str(v).strip()
            existing.append(clean)

    print(f"Read {len(existing)} existing tickets")

    # Write expanded CSV
    fieldnames = ["ticket_id", "employee_name", "issue_description", "status",
                   "exact_error_code", "created_at", "priority", "resolution_notes"]

    rows = []
    VALID_STATUSES = {"Resolved", "In Progress", "Escalated"}
    # Process existing tickets
    for i, t in enumerate(existing):
        code = t.get("exact_error_code", "").strip()
        status = t.get("status", "").strip()
        # Fix malformed rows where commas in description broke the CSV parsing
        if status not in VALID_STATUSES:
            # Reconstruct: find the real status and error code from the full row text
            all_vals = " ".join(v for v in t.values() if v)
            if "Resolved" in all_vals:
                status = "Resolved"
            elif "In Progress" in all_vals:
                status = "In Progress"
            elif "Escalated" in all_vals:
                status = "Escalated"
            else:
                status = "In Progress"
            # Fix error code too - scan for known patterns
            import re
            codes_found = re.findall(r'[A-Z]{2,}[\-][A-Z]*[\-]?\w+', all_vals)
            if codes_found:
                code = codes_found[-1]  # Last match is usually the exact_error_code
            # Reconstruct description
            desc_parts = t.get("issue_description", "")
            for k, v in t.items():
                if k not in ("ticket_id", "employee_name") and v and v not in VALID_STATUSES:
                    if v != code and v not in desc_parts:
                        desc_parts += ", " + v
            t["issue_description"] = desc_parts
        priority = SEVERITY_MAP.get(code, "P3")
        ts = rand_ts()
        res_notes = ""
        if status == "Resolved" and code in RESOLUTION_TEMPLATES:
            res_notes = RESOLUTION_TEMPLATES[code]
        elif status == "Resolved":
            res_notes = "Issue resolved after investigation and standard remediation procedures."

        rows.append({
            "ticket_id": t.get("ticket_id", "").strip(),
            "employee_name": t.get("employee_name", "").strip(),
            "issue_description": t.get("issue_description", "").strip(),
            "status": status,
            "exact_error_code": code,
            "created_at": ts,
            "priority": priority,
            "resolution_notes": res_notes,
        })

    # Sort existing by date
    rows.sort(key=lambda x: x["created_at"])

    # Generate 150 new tickets from regular employees
    new_rows = []
    for i, (desc, status, code, priority) in enumerate(NEW_TICKET_TEMPLATES):
        emp = random.choice(REG_EMPLOYEES)
        tid = f"TKT-{10101 + i}"
        ts = rand_ts()
        res_notes = ""
        if status == "Resolved" and code in RESOLUTION_TEMPLATES:
            res_notes = RESOLUTION_TEMPLATES[code]
        elif status == "Resolved":
            res_notes = "Issue resolved through standard troubleshooting procedures."

        new_rows.append({
            "ticket_id": tid,
            "employee_name": emp,
            "issue_description": desc,
            "status": status,
            "exact_error_code": code,
            "created_at": ts,
            "priority": priority,
            "resolution_notes": res_notes,
        })

    new_rows.sort(key=lambda x: x["created_at"])
    all_rows = rows + new_rows

    # Write with proper quoting
    with open(OUT_PATH, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Written {len(all_rows)} tickets to {OUT_PATH}")
    # Count stats
    statuses = {}
    for r in all_rows:
        s = r["status"]
        statuses[s] = statuses.get(s, 0) + 1
    for s, c in sorted(statuses.items()):
        print(f"  {s}: {c}")

if __name__ == "__main__":
    main()
