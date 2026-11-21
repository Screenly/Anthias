from django.db import models
from django.utils import timezone

DEFAULT_NODE_PORT = 8443


class NodeStatus(models.TextChoices):
    UNKNOWN = 'unknown', 'Not checked yet'
    ONLINE = 'online', 'Online'
    OFFLINE = 'offline', 'Offline'
    # Its push connection is up, but the controller can't call it (port
    # 8443 closed or filtered): it reports in, yet can't be published to.
    UNREACHABLE = 'unreachable', 'Connected, not reachable'
    UNAUTHORIZED = 'unauthorized', 'Left management'
    WRONG_CERT = 'wrong_cert', 'Certificate changed'


class PublishState(models.TextChoices):
    NEVER = '', 'Never published'
    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Publishing'
    OK = 'ok', 'Up to date'
    FAILED = 'failed', 'Failed'


class Node(models.Model):
    """A screen this controller manages.

    ``fingerprint`` is the lowercase hex SHA-256 of the screen's leaf
    certificate (DER), learned and verified during pairing. Every
    connection is pinned to it, so a different device answering on the
    same address is refused before the API token is sent.

    ``token_ciphertext`` is the screen-issued API token encrypted with
    the controller's key file (see :mod:`.vault`). The key file is not
    part of a backup, so a restored backup carries no usable tokens.
    """

    name = models.CharField(max_length=100)
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=DEFAULT_NODE_PORT)
    fingerprint = models.CharField(max_length=64, unique=True)
    token_ciphertext = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(
        max_length=16,
        choices=NodeStatus.choices,
        default=NodeStatus.UNKNOWN,
    )
    status_detail = models.TextField(blank=True, default='')
    last_seen_at = models.DateTimeField(blank=True, null=True)
    last_checked_at = models.DateTimeField(blank=True, null=True)
    version = models.CharField(max_length=100, blank=True, default='')
    device_model = models.CharField(max_length=200, blank=True, default='')
    # Last reading of the screen's /api/v2/info health fields (see
    # .health). Kept when the screen goes offline, but only counted
    # while it is fresh.
    health = models.JSONField(default=dict, blank=True)
    health_at = models.DateTimeField(blank=True, null=True)
    # Push connection (see .push): the id of the screen's current
    # WebSocket and when it last spoke. While it is live the status
    # poll leaves the screen alone.
    push_connection = models.CharField(max_length=32, blank=True, default='')
    push_seen_at = models.DateTimeField(blank=True, null=True)
    # The screen's node public key (from its approved CSR) and the
    # client certificate this controller's CA issued for it (see
    # .ca). The push connection must present that certificate.
    public_key_pem = models.TextField(blank=True, default='')
    client_serial = models.CharField(max_length=64, blank=True, default='')
    client_fingerprint = models.CharField(
        max_length=64, blank=True, default=''
    )
    client_not_after = models.DateTimeField(blank=True, null=True)
    # The certificate itself, so a screen that missed a renewal reply
    # can be sent it again instead of being issued yet another one.
    client_cert_pem = models.TextField(blank=True, default='')
    # The certificate a renewal replaced. It stays valid (not revoked)
    # until the screen first connects with the new one, so a renewal
    # reply lost on the way cannot lock the screen out (see .push).
    previous_client_serial = models.CharField(
        max_length=64, blank=True, default=''
    )
    previous_client_fingerprint = models.CharField(
        max_length=64, blank=True, default=''
    )
    previous_client_not_after = models.DateTimeField(blank=True, null=True)

    publish_state = models.CharField(
        max_length=16,
        choices=PublishState.choices,
        default=PublishState.NEVER,
        blank=True,
    )
    publish_detail = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'controller_nodes'
        ordering = ('name', 'id')

    def __str__(self) -> str:
        return f'{self.name} ({self.host})'

    @property
    def push_live(self) -> bool:
        from .push import is_live

        return is_live(self, timezone.now())

    @property
    def health_fresh(self) -> bool:
        from .health import is_fresh

        return is_fresh(self, timezone.now())

    @property
    def health_problems(self) -> list[str]:
        from .health import problems

        if not self.health_fresh:
            return []
        return [label for _key, label in problems(self.health)]

    @property
    def health_summary(self) -> str:
        from .health import summary_line

        return summary_line(self.health) if self.health_fresh else ''

    @property
    def base_url(self) -> str:
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'https://{host}:{self.port}'


class NodeAsset(models.Model):
    """Which remote asset on a screen mirrors which local asset.

    The screen assigns its own ``asset_id`` on create, so the controller
    has to remember the pairing to update or delete it later.
    ``identity_hash`` covers what can only change by replacing the
    remote asset (the file's SHA-256, or the URL, and the type), so an
    unchanged file is never uploaded twice. ``spec_hash`` covers the
    settings that are patched in place.
    """

    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name='assets'
    )
    source_asset_id = models.CharField(max_length=64)
    remote_asset_id = models.CharField(max_length=64)
    identity_hash = models.CharField(max_length=64, blank=True, default='')
    spec_hash = models.CharField(max_length=64, blank=True, default='')
    synced_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'controller_node_assets'
        constraints = (
            models.UniqueConstraint(
                fields=('node', 'source_asset_id'),
                name='controller_node_asset_unique_source',
            ),
        )


class AuditEntry(models.Model):
    """One fan-out action, per screen."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.CharField(max_length=150, blank=True, default='')
    action = models.CharField(max_length=32)
    node = models.ForeignKey(
        Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_entries',
    )
    node_name = models.CharField(max_length=100, blank=True, default='')
    outcome = models.CharField(max_length=16)
    detail = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'controller_audit_log'
        ordering = ('-created_at', '-id')

    def __str__(self) -> str:
        return f'{self.action} {self.node_name}: {self.outcome}'

    @property
    def display_outcome(self) -> str:
        """The outcome as shown: a join request is pending, not "ok"."""
        if self.action == 'join request' and self.outcome == 'ok':
            return 'pending'
        return self.outcome


class JoinSecret(models.Model):
    """The secret in this controller's join information.

    A single row. Reusable until regenerated, like a Proxmox cluster's
    join information, so one snippet can join several screens. Stored
    encrypted with the controller key (see :mod:`.vault`) rather than
    hashed, so the Screens page can show the snippet again.
    """

    ciphertext = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'controller_join_secret'


class JoinRequest(models.Model):
    """A screen asking to join, waiting for the operator's approval.

    Created by ``POST /api/v2/controller/join`` once the secret checks
    out and the controller has reached the screen back on its pinned
    certificate. Approving signs ``csr_pem`` and turns the request
    into a :class:`Node`; rejecting or letting it expire drops it.
    """

    request_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=DEFAULT_NODE_PORT)
    fingerprint = models.CharField(max_length=64, unique=True)
    token_ciphertext = models.TextField()
    csr_pem = models.TextField()
    # The screen's node public key (PEM SubjectPublicKeyInfo), taken
    # from the certificate that hashes to ``fingerprint`` when the
    # request arrived. Approval only signs a CSR for this key.
    public_key_pem = models.TextField(blank=True, default='')
    version = models.CharField(max_length=100, blank=True, default='')
    device_model = models.CharField(max_length=200, blank=True, default='')
    # Derived from the join secret and both fingerprints when the
    # request arrived (lib.pki.check_code); the screen shows the same.
    check_code = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    # Set while an approval is delivering the certificate, so a second
    # click (or a second tab) cannot approve the same request twice.
    approving_until = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'controller_join_requests'
        ordering = ('created_at', 'id')

    def __str__(self) -> str:
        return f'{self.name} ({self.host}), waiting for approval'

    @property
    def base_url(self) -> str:
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'https://{host}:{self.port}'


class RevokedCertificate(models.Model):
    """A client certificate this controller's CA revoked (the CRL)."""

    serial = models.CharField(max_length=64, unique=True)
    revoked_at = models.DateTimeField(default=timezone.now)
    label = models.CharField(max_length=200, blank=True, default='')
    reason = models.CharField(max_length=200, blank=True, default='')
    # Kept until it would have expired anyway; then it no longer needs
    # to be listed.
    not_after = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'controller_revoked_certificates'
        ordering = ('-revoked_at', '-id')

    def __str__(self) -> str:
        return f'{self.serial} ({self.label})'


class CrlCounter(models.Model):
    """The CRL number: one row, incremented on every re-sign.

    Screens refuse a CRL whose number is lower than the one they hold,
    so it must only ever grow, whatever the wall clock does.
    """

    number = models.BigIntegerField(default=0)

    class Meta:
        db_table = 'controller_crl_counter'
