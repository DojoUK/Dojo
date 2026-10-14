# <img width="600" height="327" alt="Dojo" src="https://github.com/user-attachments/assets/e477db25-db4f-45b8-8df3-3902b4b8e4a9" />
Open source club and class management platform. Manage members, classes, attendance, gradings, billing and payments, and documents. Self-hostable.

**Hosted version coming soon at [dojoapp.co.uk](https://dojoapp.co.uk)**

---

## What is Dojo?

Dojo is a free, open source platform for clubs, gyms and coaching centres to manage everything in one place. Built to replace spreadsheets and disconnected tools, Dojo handles your members, classes, coaches, attendance, gradings, invoicing, payments and documents, with a member-facing portal for paying and keeping up to date.

Dojo is sport and activity agnostic. Whether you run a judo club, a dance school, a boxing gym or a music centre, each club switches on only the parts it uses, records only the details it needs, and decides how it bills, takes payments and sends email.

---

## Features

### Members and classes
- **Member management:** profiles, emergency contacts, guardian support for juniors, custom fields, CSV import and export, and member pages organised into tabs
- **Class management:** schedule builder, coach assignment, enrolment, capacity limits and waiting lists
- **Attendance:** per-session registers, at-risk member detection and an analytics dashboard
- **Calendar:** month, week and list views of upcoming sessions and staff holidays
- **Public signup:** share a link for prospective members to apply, then approve or reject from Dojo
- **Announcements:** email the whole club or a single class

### Progression
- **Grades:** define your own belt, grade or level system, and record promotions with dates and notes
- **Gradings:** run grading events across one or more sessions, pick candidates from a tick list with eligibility hints, and record results that update members' grades
- **Syllabus:** a technique library shared across grades, with a syllabus for each grade

### Billing and payments
- **Invoicing:** single and bulk invoices built from each member's billing plan, with discounts, terms and kit sales
- **Automatic invoices:** each plan can make its own invoices on schedule (monthly on a chosen day, a set number of days before each term, or yearly), either sent straight away or held for an admin to check first
- **Ways to pay:** bank transfer (with the club's details and a reference), card online through the club's own Stripe account (card, Apple Pay, Google Pay), and paying in person
- **Pay now links:** every invoice email links to a payment page showing the balance due and every way to pay, and Stripe's checkout itemises what's being paid for
- **Automatic payments:** members save a card or set up a Direct Debit once, and each new invoice is collected automatically; failed payments are emailed to the member with the other ways to pay
- **Overdue reminders:** sent automatically after a chosen number of days, then repeated at a chosen interval
- **Financial reports** for the whole club

### Email
- **Choose how email is sent:** Dojo's built-in mail server (with DKIM signing, and a DNS checker for the records to add), Brevo, Amazon SES, Mailgun, Postmark, Resend, Gmail or Google Workspace, Microsoft 365, or any SMTP server, with a connection check and a test send
- **Customise every email:** change the wording of each email (welcome, portal link, invoice, payment reminder, announcement, session cancelled, waiting list place, password reset) using fields like `{first_name}` and ready-made blocks like `{pay_options}`, or replace it entirely with your own HTML. Live preview, test sends and reset to default included.
- **Branded layout:** the club's colours and logo, with an editable header and footer

### Staff and security
- **Staff roles and permissions:** org admins and coaches, plus extra permissions per coach (billing, taking payments, all attendance, messaging everyone, running gradings)
- **Two-step sign-in:** authenticator apps with recovery codes, required for all staff, admins only, or optional
- **Account controls:** admins can reset a person's two-step sign-in, email them a password reset link, or lock them out
- **Inactivity logout** after a time each club chooses
- **Qualification tracking:** DBS and coaching licence numbers and expiry dates for staff, and licences for members, each switchable
- **Audit logging:** a full change history

### Making it yours
- **Feature switches:** each club turns off what it doesn't use (gradings, syllabus, billing, inventory, documents, applications, announcements, member portal, waiting lists), and it disappears from Dojo
- **Optional fields:** choose which built-in details Dojo records
- **Appearance:** club colours and logo throughout Dojo, the sign-in pages, emails and the member portal
- **Setup wizard:** a new club picks its features, fields, payment methods and email service in one step

### Members' side
- **Member portal:** private link pages for members and guardians to see invoices, pay or set up automatic payments, and see grades and attendance
- **Document management:** waiver templates with on-screen e-signature at signup, signed PDFs, and paper waiver upload

---

## Tech Stack

- **Backend:** Django 6.1 (Python)
- **Database:** MySQL 8.4
- **Frontend:** Django templates, HTMX and Bootstrap 5
- **Payments:** Stripe (each club's own account)
- **Runs in Docker:** `web` (the site), `db` (MySQL), `scheduler` (automatic payments, invoices and reminders) and `mail` (the built-in mail server)
- **Licence:** AGPL-3.0

---

## Getting Started

Dojo runs in Docker. You'll need [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) installed. No `.env` file, migration command or `createsuperuser` step is needed: the containers generate their own secret key, wait for the database, and migrate themselves on first start.

```bash
git clone https://github.com/DojoUK/Dojo.git
cd Dojo

# Start Dojo
docker compose up -d
```

Open [http://localhost:8000/](http://localhost:8000/). With no club set up yet, you'll land on the setup wizard, which creates your club and admin account in one step and lets you choose its features, fields, payment methods and email service. New clubs ask staff to set up two-step sign-in, which you can relax under Settings → Security.

Want to look around with sample data first? Visit `/setup/?demo=1` for a one-click demo club.

**Stopping Dojo:**

```bash
docker compose down
```

For a real domain, HTTPS, backups, email deliverability, payments and the environment variable reference, see the [self-hosting guide](./SELF_HOSTING.md).

### Updating

```bash
git pull
docker compose up -d --build
```

Migrations run automatically. Upgrading from a version that used MySQL 8.0 moves your database to MySQL 8.4, which can't be undone, so **take a backup first**. The [self-hosting guide](./SELF_HOSTING.md#upgrading-from-mysql-80-to-84) has the steps.

### Testing emails and payments locally

`docker compose --profile dev up -d mailpit` starts [Mailpit](https://mailpit.axllent.org/), which catches every email Dojo sends and shows it at http://localhost:8025. Card and automatic payments can be tried end to end with Stripe's test mode and the bundled `stripe` forwarder. The [self-hosting guide](./SELF_HOSTING.md#testing-emails-and-payments-locally) walks through both.

---

## Roadmap

### Done
- [x] Multi-club architecture, with per-club feature switches and optional fields
- [x] Members with guardians, custom fields, and CSV import and export
- [x] Classes with schedule builder, capacity and waiting lists
- [x] Attendance registers and analytics
- [x] Staff with roles, per-coach permissions, and qualification tracking
- [x] Grades, multi-session gradings, and a syllabus and technique library
- [x] Invoicing, bulk invoicing, and automatic invoices on schedule
- [x] Payments by bank transfer, card (each club's own Stripe account) or in person, with Pay now links
- [x] Automatic payments by saved card or Direct Debit
- [x] Automatic overdue reminders
- [x] Choice of email service, including a built-in mail server, and fully customisable emails
- [x] Two-step sign-in and admin account controls
- [x] Member portal, document management and signup e-signature
- [x] Public signup and approval
- [x] Calendar, financial reports and audit logging

### Planned
- [ ] Passkeys
- [ ] Teaching records against the syllabus, and knowledge gaps per member
- [ ] Register upgrades (session leaders, helpers, quick add)
- [ ] Calendar colours
- [ ] Credit balances for overpayments
- [ ] Expenses and forecasting
- [ ] More CSV importers
- [ ] A signup API for club websites
- [ ] Themes and dark mode
- [ ] A separate member portal service
- [ ] Hosted version at [dojoapp.co.uk](https://dojoapp.co.uk)

---

## Data & Privacy

Dojo processes member personal data, including medical information and, for staff, DBS and safeguarding numbers. Card payments go through **Stripe**, using each club's own Stripe account: card details never touch Dojo. Email goes through whichever service the club chooses, or Dojo's own mail server. Stripe keys and email passwords are stored encrypted. See [PRIVACY.md](./PRIVACY.md) for lawful basis, retention periods and the processor list.

---

## Contributing

Dojo is in active development. Contributions, ideas and feedback are welcome.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes, with tests
4. Run the tests: `docker compose exec web python manage.py test`
5. Open a pull request and fill in the [pull request checklist](./.github/PULL_REQUEST_TEMPLATE.md)

The checklist asks for a few things worth knowing up front:
- Run new tests against the old code first, to show they can fail.
- Check that one club can never reach another club's data.
- Money is always `Decimal`.
- Tests never reach Stripe or send real email.

Please open an issue before starting any significant work, so we can discuss it first.

---

## Licence

Dojo is licensed under the [GNU Affero General Public License v3.0](./LICENSE).

You are free to self-host and modify Dojo. If you run a modified version as a service over a network, you must make your modifications available under the same licence.

A hosted version will be available at [dojoapp.co.uk](https://dojoapp.co.uk).
