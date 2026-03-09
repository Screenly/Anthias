"""Tests for schedule slot API endpoints."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from anthias_app.models import (
    Asset,
    ScheduleSlot,
    ScheduleSlotItem,
)


class ScheduleSlotAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.asset = Asset.objects.create(
            name='Test Asset',
            uri='https://example.com',
            mimetype='web',
            duration=10,
            is_enabled=True,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=1),
        )

    def test_list_slots_empty(self):
        url = reverse('api:schedule_slot_list')
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data, [])

    def test_create_time_slot(self):
        url = reverse('api:schedule_slot_list')
        response = self.client.post(
            url,
            {
                'name': 'Morning',
                'slot_type': 'time',
                'time_from': '09:00',
                'time_to': '12:00',
            },
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(response.data['name'], 'Morning')
        self.assertEqual(response.data['slot_type'], 'time')

    def test_create_default_slot(self):
        url = reverse('api:schedule_slot_list')
        response = self.client.post(
            url,
            {
                'name': 'Fallback',
                'slot_type': 'default',
                'is_default': True,
            },
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertTrue(response.data['is_default'])

    def test_only_one_default_allowed(self):
        url = reverse('api:schedule_slot_list')
        self.client.post(
            url,
            {
                'name': 'Default 1',
                'slot_type': 'default',
                'is_default': True,
            },
        )
        response = self.client.post(
            url,
            {
                'name': 'Default 2',
                'slot_type': 'default',
                'is_default': True,
            },
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_get_slot_detail(self):
        slot = ScheduleSlot.objects.create(
            name='Test',
            slot_type='time',
            time_from='08:00',
            time_to='10:00',
        )
        url = reverse(
            'api:schedule_slot_detail',
            kwargs={'slot_id': slot.slot_id},
        )
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data['name'], 'Test')

    def test_update_slot(self):
        slot = ScheduleSlot.objects.create(
            name='Old',
            slot_type='time',
            time_from='08:00',
            time_to='10:00',
        )
        url = reverse(
            'api:schedule_slot_detail',
            kwargs={'slot_id': slot.slot_id},
        )
        response = self.client.put(
            url,
            {'name': 'New'},
            format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(response.data['name'], 'New')

    def test_delete_slot(self):
        slot = ScheduleSlot.objects.create(
            name='Delete Me',
            slot_type='time',
            time_from='08:00',
            time_to='10:00',
        )
        url = reverse(
            'api:schedule_slot_detail',
            kwargs={'slot_id': slot.slot_id},
        )
        response = self.client.delete(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertFalse(
            ScheduleSlot.objects.filter(
                slot_id=slot.slot_id,
            ).exists(),
        )

    def test_slot_not_found(self):
        url = reverse(
            'api:schedule_slot_detail',
            kwargs={'slot_id': 'nonexistent'},
        )
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_404_NOT_FOUND,
        )


class ScheduleSlotItemAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.asset = Asset.objects.create(
            name='Test Asset',
            uri='https://example.com',
            mimetype='web',
            duration=10,
            is_enabled=True,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=1),
        )
        self.slot = ScheduleSlot.objects.create(
            name='Default',
            slot_type='default',
            is_default=True,
        )

    def test_add_item_to_slot(self):
        url = reverse(
            'api:schedule_slot_items',
            kwargs={'slot_id': self.slot.slot_id},
        )
        response = self.client.post(
            url,
            {'asset_id': self.asset.asset_id},
            format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            response.data['asset_id'],
            self.asset.asset_id,
        )

    def test_duplicate_item_rejected(self):
        ScheduleSlotItem.objects.create(
            slot=self.slot,
            asset=self.asset,
        )
        url = reverse(
            'api:schedule_slot_items',
            kwargs={'slot_id': self.slot.slot_id},
        )
        response = self.client.post(
            url,
            {'asset_id': self.asset.asset_id},
            format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_409_CONFLICT,
        )

    def test_list_items(self):
        ScheduleSlotItem.objects.create(
            slot=self.slot,
            asset=self.asset,
        )
        url = reverse(
            'api:schedule_slot_items',
            kwargs={'slot_id': self.slot.slot_id},
        )
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(len(response.data), 1)

    def test_delete_item(self):
        item = ScheduleSlotItem.objects.create(
            slot=self.slot,
            asset=self.asset,
        )
        url = reverse(
            'api:schedule_slot_item_detail',
            kwargs={
                'slot_id': self.slot.slot_id,
                'item_id': item.item_id,
            },
        )
        response = self.client.delete(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )

    def test_reorder_items(self):
        asset2 = Asset.objects.create(
            name='Asset 2',
            uri='https://example2.com',
            mimetype='web',
            duration=15,
            is_enabled=True,
            start_date=timezone.now() - timedelta(days=1),
            end_date=timezone.now() + timedelta(days=1),
        )
        item1 = ScheduleSlotItem.objects.create(
            slot=self.slot,
            asset=self.asset,
            sort_order=0,
        )
        item2 = ScheduleSlotItem.objects.create(
            slot=self.slot,
            asset=asset2,
            sort_order=1,
        )
        url = reverse(
            'api:schedule_slot_items_order',
            kwargs={'slot_id': self.slot.slot_id},
        )
        response = self.client.post(
            url,
            {'ids': [item2.item_id, item1.item_id]},
            format='json',
        )
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        item1.refresh_from_db()
        item2.refresh_from_db()
        self.assertEqual(item2.sort_order, 0)
        self.assertEqual(item1.sort_order, 1)


class ScheduleStatusAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_status_no_slots(self):
        url = reverse('api:schedule_status')
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertFalse(response.data['schedule_enabled'])

    def test_status_with_default_slot(self):
        ScheduleSlot.objects.create(
            name='Default',
            slot_type='default',
            is_default=True,
        )
        url = reverse('api:schedule_status')
        response = self.client.get(url)
        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )
        self.assertTrue(response.data['schedule_enabled'])
        self.assertTrue(response.data['using_default'])
