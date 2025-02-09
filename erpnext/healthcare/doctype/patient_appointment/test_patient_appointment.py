# -*- coding: utf-8 -*-
# Copyright (c) 2015, ESS LLP and Contributors
# See license.txt
from __future__ import unicode_literals
import unittest
import frappe
from erpnext.healthcare.doctype.patient_appointment.patient_appointment import update_status, make_encounter, check_payment_fields_reqd, check_is_new_patient
from frappe.utils import nowdate, add_days, now_datetime
from frappe.utils.make_random import get_random
from erpnext.accounts.doctype.pos_profile.test_pos_profile import make_pos_profile


class TestPatientAppointment(unittest.TestCase):
	def setUp(self):
		frappe.db.sql("""delete from `tabPatient Appointment`""")
		frappe.db.sql("""delete from `tabFee Validity`""")
		frappe.db.sql("""delete from `tabPatient Encounter`""")
		make_pos_profile()

	def test_status(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 0)
		appointment = create_appointment(patient, practitioner, nowdate())
		self.assertEqual(appointment.status, 'Open')
		appointment = create_appointment(patient, practitioner, add_days(nowdate(), 2))
		self.assertEqual(appointment.status, 'Scheduled')
		encounter = create_encounter(appointment)
		self.assertEqual(frappe.db.get_value('Patient Appointment', appointment.name, 'status'), 'Closed')
		encounter.cancel()
		self.assertEqual(frappe.db.get_value('Patient Appointment', appointment.name, 'status'), 'Open')

	def test_start_encounter(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		appointment = create_appointment(patient, practitioner, add_days(nowdate(), 4), invoice = 1)
		appointment.reload()
		self.assertEqual(appointment.invoiced, 1)
		encounter = make_encounter(appointment.name)
		self.assertTrue(encounter)
		self.assertEqual(encounter.company, appointment.company)
		self.assertEqual(encounter.practitioner, appointment.practitioner)
		self.assertEqual(encounter.patient, appointment.patient)
		# invoiced flag mapped from appointment
		self.assertEqual(encounter.invoiced, frappe.db.get_value('Patient Appointment', appointment.name, 'invoiced'))

	def test_auto_invoicing(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 0)
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 0)
		appointment = create_appointment(patient, practitioner, nowdate())
		self.assertEqual(frappe.db.get_value('Patient Appointment', appointment.name, 'invoiced'), 0)

		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		appointment = create_appointment(patient, practitioner, add_days(nowdate(), 2), invoice=1)
		self.assertEqual(frappe.db.get_value('Patient Appointment', appointment.name, 'invoiced'), 1)
		sales_invoice_name = frappe.db.get_value('Sales Invoice Item', {'reference_dn': appointment.name}, 'parent')
		self.assertTrue(sales_invoice_name)
		self.assertEqual(frappe.db.get_value('Sales Invoice', sales_invoice_name, 'company'), appointment.company)
		self.assertEqual(frappe.db.get_value('Sales Invoice', sales_invoice_name, 'patient'), appointment.patient)
		self.assertEqual(frappe.db.get_value('Sales Invoice', sales_invoice_name, 'paid_amount'), appointment.paid_amount)

	def test_auto_invoicing_based_on_department(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 0)
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		appointment_type = create_appointment_type()

		appointment = create_appointment(patient, practitioner, add_days(nowdate(), 2),
			invoice=1, appointment_type=appointment_type.name, department='_Test Medical Department')
		appointment.reload()

		self.assertEqual(appointment.invoiced, 1)
		self.assertEqual(appointment.billing_item, 'HLC-SI-001')
		self.assertEqual(appointment.paid_amount, 200)

		sales_invoice_name = frappe.db.get_value('Sales Invoice Item', {'reference_dn': appointment.name}, 'parent')
		self.assertTrue(sales_invoice_name)
		self.assertEqual(frappe.db.get_value('Sales Invoice', sales_invoice_name, 'paid_amount'), appointment.paid_amount)

	def test_auto_invoicing_according_to_appointment_type_charge(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 0)
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)

		item = create_healthcare_service_items()
		items = [{
				'op_consulting_charge_item': item,
				'op_consulting_charge': 300
		}]
		appointment_type = create_appointment_type(args={
				'name': 'Generic Appointment Type charge',
				'items': items
			})

		appointment = create_appointment(patient, practitioner, add_days(nowdate(), 2),
			invoice=1, appointment_type=appointment_type.name)
		appointment.reload()

		self.assertEqual(appointment.invoiced, 1)
		self.assertEqual(appointment.billing_item, item)
		self.assertEqual(appointment.paid_amount, 300)

		sales_invoice_name = frappe.db.get_value('Sales Invoice Item', {'reference_dn': appointment.name}, 'parent')
		self.assertTrue(sales_invoice_name)

	def test_appointment_cancel(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 1)
		appointment = create_appointment(patient, practitioner, nowdate())
		fee_validity = frappe.db.get_value('Fee Validity', {'patient': patient, 'practitioner': practitioner})
		# fee validity created
		self.assertTrue(fee_validity)

		# first follow up appointment
		appointment = create_appointment(patient, practitioner, nowdate())
		self.assertEqual(frappe.db.get_value('Fee Validity', fee_validity, 'visited'), 1)

		update_status(appointment.name, 'Cancelled')
		# check fee validity updated
		self.assertEqual(frappe.db.get_value('Fee Validity', fee_validity, 'visited'), 0)

		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 0)
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		appointment = create_appointment(patient, practitioner, nowdate(), invoice=1)
		update_status(appointment.name, 'Cancelled')
		# check invoice cancelled
		sales_invoice_name = frappe.db.get_value('Sales Invoice Item', {'reference_dn': appointment.name}, 'parent')
		self.assertEqual(frappe.db.get_value('Sales Invoice', sales_invoice_name, 'status'), 'Cancelled')

	def test_appointment_booking_for_admission_service_unit(self):
		from erpnext.healthcare.doctype.inpatient_record.inpatient_record import admit_patient, discharge_patient, schedule_discharge
		from erpnext.healthcare.doctype.inpatient_record.test_inpatient_record import \
			create_inpatient, get_healthcare_service_unit, mark_invoiced_inpatient_occupancy

		frappe.db.sql("""delete from `tabInpatient Record`""")
		patient, medical_department, practitioner = create_healthcare_docs()
		patient = create_patient()
		# Schedule Admission
		ip_record = create_inpatient(patient)
		ip_record.expected_length_of_stay = 0
		ip_record.save(ignore_permissions = True)

		# Admit
		service_unit = get_healthcare_service_unit('Test Service Unit Ip Occupancy')
		admit_patient(ip_record, service_unit, now_datetime())

		appointment = create_appointment(patient, practitioner, nowdate(), service_unit=service_unit)
		self.assertEqual(appointment.service_unit, service_unit)

		# Discharge
		schedule_discharge(frappe.as_json({'patient': patient}))
		ip_record1 = frappe.get_doc("Inpatient Record", ip_record.name)
		mark_invoiced_inpatient_occupancy(ip_record1)
		discharge_patient(ip_record1)

	def test_invalid_healthcare_service_unit_validation(self):
		from erpnext.healthcare.doctype.inpatient_record.inpatient_record import admit_patient, discharge_patient, schedule_discharge
		from erpnext.healthcare.doctype.inpatient_record.test_inpatient_record import \
			create_inpatient, get_healthcare_service_unit, mark_invoiced_inpatient_occupancy

		frappe.db.sql("""delete from `tabInpatient Record`""")
		patient, medical_department, practitioner = create_healthcare_docs()
		patient = create_patient()
		# Schedule Admission
		ip_record = create_inpatient(patient)
		ip_record.expected_length_of_stay = 0
		ip_record.save(ignore_permissions = True)

		# Admit
		service_unit = get_healthcare_service_unit('Test Service Unit Ip Occupancy')
		admit_patient(ip_record, service_unit, now_datetime())

		appointment_service_unit = get_healthcare_service_unit('Test Service Unit Ip Occupancy for Appointment')
		appointment = create_appointment(patient, practitioner, nowdate(), service_unit=appointment_service_unit, save=0)
		self.assertRaises(frappe.exceptions.ValidationError, appointment.save)

		# Discharge
		schedule_discharge(frappe.as_json({'patient': patient}))
		ip_record1 = frappe.get_doc("Inpatient Record", ip_record.name)
		mark_invoiced_inpatient_occupancy(ip_record1)
		discharge_patient(ip_record1)

	def test_payment_should_be_mandatory_for_new_patient_appointment(self):
		frappe.db.set_value('Healthcare Settings', None, 'enable_free_follow_ups', 1)
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		frappe.db.set_value('Healthcare Settings', None, 'max_visits', 3)
		frappe.db.set_value('Healthcare Settings', None, 'valid_days', 30)

		patient = create_patient()
		assert check_is_new_patient(patient)
		payment_required = check_payment_fields_reqd(patient)
		assert payment_required is True

	def test_sales_invoice_should_be_generated_for_new_patient_appointment(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		frappe.db.set_value('Healthcare Settings', None, 'automate_appointment_invoicing', 1)
		invoice_count = frappe.db.count('Sales Invoice')

		assert check_is_new_patient(patient)
		create_appointment(patient, practitioner, nowdate())
		new_invoice_count = frappe.db.count('Sales Invoice')

		assert new_invoice_count == invoice_count + 1

	def test_patient_appointment_should_consider_permissions_while_fetching_appointments(self):
		patient, medical_department, practitioner = create_healthcare_docs()
		create_appointment(patient, practitioner, nowdate())

		patient, medical_department, new_practitioner = create_healthcare_docs(practitioner_name='Dr. John')
		create_appointment(patient, new_practitioner, nowdate())

		roles = [{"doctype": "Has Role", "role": "Physician"}]
		user = create_user(roles=roles)
		new_practitioner = frappe.get_doc('Healthcare Practitioner', new_practitioner)
		new_practitioner.user_id = user.email
		new_practitioner.save()

		frappe.set_user(user.name)
		appointments = frappe.get_list('Patient Appointment')
		assert len(appointments) == 1

		frappe.set_user("Administrator")
		appointments = frappe.get_list('Patient Appointment')
		assert len(appointments) == 2

def create_healthcare_docs(practitioner_name=None):
	if not practitioner_name:
		practitioner_name = '_Test Healthcare Practitioner'

	patient = create_patient()
	practitioner = frappe.db.exists('Healthcare Practitioner', practitioner_name)
	medical_department = frappe.db.exists('Medical Department', '_Test Medical Department')

	if not medical_department:
		medical_department = frappe.new_doc('Medical Department')
		medical_department.department = '_Test Medical Department'
		medical_department.save(ignore_permissions=True)
		medical_department = medical_department.name

	if not practitioner:
		practitioner = frappe.new_doc('Healthcare Practitioner')
		practitioner.first_name = practitioner_name
		practitioner.gender = 'Female'
		practitioner.department = medical_department
		practitioner.op_consulting_charge = 500
		practitioner.inpatient_visit_charge = 500
		practitioner.save(ignore_permissions=True)
		practitioner = practitioner.name

	return patient, medical_department, practitioner

def create_patient():
	patient = frappe.db.exists('Patient', '_Test Patient')
	if not patient:
		patient = frappe.new_doc('Patient')
		patient.first_name = '_Test Patient'
		patient.sex = 'Female'
		patient.save(ignore_permissions=True)
		patient = patient.name
	return patient

def create_encounter(appointment):
	if appointment:
		encounter = frappe.new_doc('Patient Encounter')
		encounter.appointment = appointment.name
		encounter.patient = appointment.patient
		encounter.practitioner = appointment.practitioner
		encounter.encounter_date = appointment.appointment_date
		encounter.encounter_time = appointment.appointment_time
		encounter.company = appointment.company
		encounter.save()
		encounter.submit()
		return encounter

def create_appointment(patient, practitioner, appointment_date, invoice=0, procedure_template=0,
	service_unit=None, appointment_type=None, save=1, department=None):
	item = create_healthcare_service_items()
	frappe.db.set_value('Healthcare Settings', None, 'inpatient_visit_charge_item', item)
	frappe.db.set_value('Healthcare Settings', None, 'op_consulting_charge_item', item)
	appointment = frappe.new_doc('Patient Appointment')
	appointment.patient = patient
	appointment.practitioner = practitioner
	appointment.department = department or '_Test Medical Department'
	appointment.appointment_date = appointment_date
	appointment.company = '_Test Company'
	appointment.duration = 15
	if service_unit:
		appointment.service_unit = service_unit
	if invoice:
		appointment.mode_of_payment = 'Cash'
	if appointment_type:
		appointment.appointment_type = appointment_type
	if procedure_template:
		appointment.procedure_template = create_clinical_procedure_template().get('name')
	if save:
		appointment.save(ignore_permissions=True)
	return appointment

def create_healthcare_service_items():
	if frappe.db.exists('Item', 'HLC-SI-001'):
		return 'HLC-SI-001'
	item = frappe.new_doc('Item')
	item.item_code = 'HLC-SI-001'
	item.item_name = 'Consulting Charges'
	item.item_group = 'Services'
	item.is_stock_item = 0
	item.stock_uom = 'Nos'
	item.save()
	return item.name

def create_clinical_procedure_template():
	if frappe.db.exists('Clinical Procedure Template', 'Knee Surgery and Rehab'):
		return frappe.get_doc('Clinical Procedure Template', 'Knee Surgery and Rehab')
	template = frappe.new_doc('Clinical Procedure Template')
	template.template = 'Knee Surgery and Rehab'
	template.item_code = 'Knee Surgery and Rehab'
	template.item_group = 'Services'
	template.is_billable = 1
	template.description = 'Knee Surgery and Rehab'
	template.rate = 50000
	template.save()
	return template

def create_appointment_type(args=None):
	if not args:
		args =  frappe.local.form_dict

	name = args.get('name') or 'Test Appointment Type wise Charge'

	if frappe.db.exists('Appointment Type', name):
		return frappe.get_doc('Appointment Type', name)

	else:
		item = create_healthcare_service_items()
		items = [{
				'medical_department': '_Test Medical Department',
				'op_consulting_charge_item': item,
				'op_consulting_charge': 200
		}]
		return frappe.get_doc({
			'doctype': 'Appointment Type',
			'appointment_type': args.get('name') or 'Test Appointment Type wise Charge',
			'default_duration': args.get('default_duration') or 20,
			'color': args.get('color') or '#7575ff',
			'price_list': args.get('price_list') or frappe.db.get_value("Price List", {"selling": 1}),
			'items': args.get('items') or items
		}).insert()

def create_user(email=None, roles=None):
	if not email:
		email = '{}@frappe.com'.format(frappe.utils.random_string(10))
	user = frappe.db.exists('User', email)
	if not user:
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": "test_user",
			"password": "password",
			"roles": roles,
		}).insert()
	return user
