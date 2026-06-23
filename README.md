# <img width="600" height="327" alt="Dojo" src="https://github.com/user-attachments/assets/e477db25-db4f-45b8-8df3-3902b4b8e4a9" />
Open source club and class management platform. Manage members, attendance, billing, and documents. Self-hostable or run as a SaaS.

**Hosted version coming soon at [dojoapp.co.uk](https://dojoapp.co.uk)**

---

## What is Dojo?

Dojo is a free, open source platform for clubs, gyms, and coaching centres to manage everything in one place. Built to replace spreadsheets and disconnected tools, Dojo handles your members, classes, coaches, attendance, invoicing, and documents — with a member-facing portal for payments and communication.

Dojo is sport and activity agnostic. Whether you run a judo club, a dance school, a boxing gym, or a music centre, Dojo adapts to your needs.

---

## Features

- **Multi-organisation** — run multiple clubs or centres from a single instance
- **Member management** — profiles, emergency contacts, guardian support for juniors, custom fields, CSV import/export
- **Class management** — schedule builder, coach assignment, enrolment, capacity limits and waiting lists
- **Attendance** — per-session registers, at-risk member detection, analytics dashboard
- **Billing and invoicing** — generate invoices, record payments, bulk monthly invoicing, Stripe online payments and autopay subscriptions
- **Member portal** — tokenised self-service pages for members and guardians: view invoices, pay online, see grade history and attendance
- **Announcements** — bulk email all members or a specific class
- **Progression / grading** — define your own belt, grade, or level system; record promotions with dates and notes
- **Licence and qualification tracking** — track member licence numbers and expiry dates; track staff DBS and coaching licences with expiry alerts
- **Calendar** — visual month/week/list view of all upcoming sessions
- **Document management** — upload and store documents per member; waiver templates with canvas e-signature on signup, stamped signed PDFs, offline paper waiver upload
- **Custom fields** — add your own fields to member profiles
- **Public signup** — share a link for prospective members to apply; approve or reject from the admin
- **Audit logging** — full change history across members, classes, and attendance
- **Self-hostable** — deploy on your own infrastructure and own your data

---

## Tech Stack

- **Backend** — Django (Python)
- **Database** — MySQL
- **Frontend** — Django Templates + HTMX + Bootstrap 5
- **Payments** — Stripe
- **Licence** — AGPL-3.0

---

## Getting Started

Dojo runs in Docker. You'll need [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) installed.

```bash
git clone https://github.com/DojoUK/dojo.git
cd dojo

# Copy the example env file and fill in your values
cp .env.example .env

# Start the database and web server
docker compose up -d

# Run database migrations
docker compose exec web python manage.py migrate

# Create your admin account
docker compose exec web python manage.py createsuperuser
```

Then open [http://localhost:8000/admin/](http://localhost:8000/admin/) to create your first organisation, then head to [http://localhost:8000/](http://localhost:8000/) to log in.

**Stopping Dojo:**

```bash
docker compose down
```

---

## Roadmap

- [x] Core multi-tenant architecture
- [x] Member management with guardian support, custom fields, CSV import/export
- [x] Class management with schedule builder, capacity, and waiting lists
- [x] Attendance register and analytics
- [x] Staff and coach management with DBS/qualification tracking
- [x] Billing, invoicing, and Stripe payments + autopay subscriptions
- [x] Member tokenised portal
- [x] Progression and grading system
- [x] Document management
- [x] Email notifications (welcome, invoice, cancellation, announcements)
- [x] Public membership signup and approval flow
- [x] Calendar view and financial reports
- [x] Audit logging
- [ ] Stripe Connect (per-organisation payment accounts)
- [x] Canvas e-signature on signup with PDF stamping
- [ ] DocuSeal integration (not currently planned — canvas signing covers the use case)
- [ ] S3 / R2 file storage
- [ ] Hosted SaaS — [dojoapp.co.uk](https://dojoapp.co.uk) (coming soon)

---

## Contributing

Dojo is in early development. Contributions, ideas, and feedback are welcome.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Open a pull request

Please open an issue before starting any significant work so we can discuss it first.

---

## Licence

Dojo is licensed under the [GNU Affero General Public License v3.0](./LICENSE).

You are free to self-host and modify Dojo. If you run a modified version as a service over a network, you must make your modifications available under the same licence.

A hosted version will be available at [dojoapp.co.uk](https://dojoapp.co.uk).
