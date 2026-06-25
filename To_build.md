

## 🗺️ Project Implementation Roadmap

### Stage 1: Security Fixes & Core Bug Resolution

This stage focuses on closing immediate security vulnerabilities, sanitizing errors, and fixing basic platform navigation behaviors.

* **[FE-1] Auth Token Security**
* *Task:* Migrate token storage from `localStorage` to `HttpOnly`, `SameSite=Strict`, and `Secure` cookies.
* *Action Item:* Update the token retrieval logic in `api.js`. done


* **[FE-4] API Error Sanitisation**
* *Task:* Implement a global error interceptor in `api.js`.
* *Action Item:* Ensure all network or system failures return clean, sanitized, user-facing error messages instead of raw stack traces.   done


* **[FE-6] Login Redirect Fix**
* *Task:* Fix the authentication fallback loop in `LoginPage.jsx`.
* *Action Item:* Append a redirect parameter (e.g., `/login?redirect=/platform`) and restore the intended path upon successful login. done


* **[BE-5] JWT Revocation & Lifecycle**
* *Task:* Implement a token blacklist system in `auth.py`.
* *Action Item:* Enforce short-lived access tokens combined with refresh token rotation. done



---

### Stage 2: Database Migration & Additional Features

This stage covers database overhauls, backend data isolation features, multi-tenancy architecture, and UI/UX improvements.

#### 🗄️ Database & Structural Migration

* **[BE-1] PostgreSQL Migration**
* *Task:* Perform a full schema migration moving from SQLite to PostgreSQL via SQLAlchemy.
* *Action Item:* Run data integrity validations and execute database index optimization.   done


* **[S3-1] Cloud Storage Migration**
* *Task:* Move local file uploads to AWS S3.
* *Action Item:* Configure a private bucket, set up IAM role-based access control, utilize pre-signed URLs, and separate buckets by environment.   done



#### 🛡️ Backend Multi-Tenancy & Security Enhancements

* **[BE-2] Simpro Token Encryption**
* *Task:* Apply symmetric encryption (Fernet) to sensitive Simpro credentials.
* *Action Item:* Enforce encryption on database write and decryption on read within `chat.py` and `superadmin.py`.   done 


* **[BE-3] MYOB Multi-Tenancy**
* *Task:* Store per-tenant MYOB server URLs at the organizational level.
* *Action Item:* Ensure these URLs resolve dynamically at request time.  out of scope


* **[BE-4] Tenant Isolation**
* *Task:* Implement ORM-level row-level scoping using SQLAlchemy.
* *Action Item:* Heavily enforce `org_id` restrictions at the core model layer to prevent cross-tenant data leaks.  done



#### 🖥️ Frontend Performance & UI Features

* **[FE-2] Pagination & Filtering**
* *Task:* Build server-side pagination, search debouncing, and status filtering.
* *Action Item:* Apply these to both the **Tenants List Page** and the **Users List Page**. 


* **[FE-3] Simpro Token Status Badge**
* *Task:* Add a read-only metadata badge displaying the setup status (`Configured` / `Missing`).
* *Action Item:* Place it directly next to the credential fields on the **Tenant Detail Page**.


* **[FE-5] State Management Upgrade**
* *Task:* Introduce TanStack Query (React Query) into the application frontend.
* *Action Item:* Use it for robust cache management and silent background data refetching.



---

### Stage 3: Infrastructure & Cloud Deployment

The final stage establishes stable, isolated, automated, and observable multi-tenant cloud environments on AWS.

* **[1] Secrets & Environment Management**
* *Task:* Comprehensive audit of the codebase to strip hardcoded values.
* *Action Item:* Integrate AWS Secrets Manager or SSM Parameter Store across all running services.


* **[2] Containerization / Dockerisation**
* *Task:* Construct production-grade Dockerfiles for the entire stack (3 backend services, 2 frontend services).
* *Action Item:* Utilize multi-stage builds to minimize image sizes and stitch them together using `docker-compose`.


* **[3] AWS EC2 Auto-Scaling & Load Balancing**
* *Task:* Provision high-availability AWS infrastructure designed for 250+ concurrent users.
* *Action Item:* Create Launch Templates, configure Auto Scaling Groups, and route traffic via an Application Load Balancer (ALB).


* **[C] Subdomain Mapping (DNS Routing)**
* *Task:* Build branded entry points for clients.
* *Action Item:* Configure Wildcard DNS CNAME records, implement frontend subdomain routing, create a branded login layout, and map a backend `tenant-info` endpoint.


* **[4] Dev & Production Isolation**
* *Task:* Deploy isolated staging and production target environments.
* *Action Item:* Separate configurations completely, assigning dedicated PostgreSQL instances and distinct S3 buckets to each.


* **[6] Multi-Tenant Configuration Verification**
* *Task:* Verify environment configurations deploy flawlessly as structured in the code footprint.
* *Action Item:* Confirm per-tenant MYOB server URLs are functioning seamlessly post-deployment.


* **[5] CloudWatch Logging & Observability**
* *Task:* Set up centralized, structured logging across all modular services.
* *Action Item:* Establish real-time error monitoring and cloud alert notification pipelines.


* **[7] Documentation & Handover**
* *Task:* Final project sign-off phase.
* *Action Item:* Deliver complete deployment runbooks, clear system architecture diagrams, troubleshooting playbooks, and automated CI/CD pipeline documentation.