#!/usr/bin/env python3
"""
Oracle VPS Auto Provisioner - Version 2026

Objetivo:
  - Region fija: eu-frankfurt-1 (Germany Central)
  - Shape: VM.Standard.A1.Flex (Ampere ARM), 2 OCPU / 12 GB RAM
  - Imagen: Canonical Ubuntu 24.04 Minimal aarch64 (buscada automaticamente)
  - Probar las 3 Availability Domains (AD-1, AD-2, AD-3) en cada ejecucion
  - Si Oracle responde "Out of capacity": reintentar en la siguiente vuelta y
    terminar con exito (exit 0), sin marcar el workflow como fallido
  - Si la instancia se crea: enviar email por SMTP de Gmail (smtp.gmail.com:465)
"""

import os
import sys
import time
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import oci

# ---------------------------------------------------------------------------
# Configuracion base
# ---------------------------------------------------------------------------
REGION = 'eu-frankfurt-1'
SHAPE = 'VM.Standard.A1.Flex'
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 465

# Tiempo maximo sondeando dentro de una sola ejecucion del workflow.
# El cron de GitHub no se cumple cada 10 minutos en la practica (suele lanzar
# una ejecucion cada varias horas), asi que cada ejecucion sondea durante un
# periodo largo para no perder las ventanas de capacidad.
MAX_RUNTIME_SECONDS = int(os.getenv('MAX_RUNTIME_SECONDS', '19800'))  # 5h30m
RETRY_DELAY_SECONDS = int(os.getenv('RETRY_DELAY_SECONDS', '60'))

# ---------------------------------------------------------------------------
# Logging (a archivo + stdout). Nunca se registran secretos.
# ---------------------------------------------------------------------------
log_dir = os.path.expanduser('~/.oci')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'provision.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def is_out_of_capacity_error(service_error):
    """Detecta si un ServiceError es por falta de capacidad (no es un error real)."""
    code = str(getattr(service_error, 'code', '') or '').strip().lower()
    status = getattr(service_error, 'status', None)
    raw_message = str(getattr(service_error, 'message', '') or '')
    message = ' '.join(raw_message.lower().split())
    normalized_message = message.rstrip(' .:;!?')

    capacity_codes = (
        'outhostcapacity',
        'outhostcapacityexception',
        'outofhostcapacity',
    )
    if code in capacity_codes:
        return True

    # Caso real observado en OCI Frankfurt:
    #   code=InternalError, status=500, message='Out of host capacity.'
    if code == 'internalerror' and status == 500:
        if 'out of host capacity' in normalized_message or 'out of capacity' in normalized_message:
            return True

    if 'out of host capacity' in message or 'out of capacity' in message:
        return True

    return False


class OracleVPSProvisioner:
    def __init__(self):
        self.config = self.load_config()
        self.image_id = None
        self.region_subscribed = None

    # ------------------------------------------------------------------
    # Configuracion / credenciales
    # ------------------------------------------------------------------
    def load_config(self):
        config = {
            'user_ocid': os.getenv('ORACLE_USER_OCID'),
            'tenancy_ocid': os.getenv('ORACLE_TENANCY_OCID'),
            'region': REGION,
            'compartment_ocid': os.getenv('ORACLE_COMPARTMENT_OCID'),
            'subnet_ocid': os.getenv('ORACLE_SUBNET_OCID'),
            'api_key_content': os.getenv('ORACLE_API_KEY'),
            'api_fingerprint': os.getenv('ORACLE_API_FINGERPRINT'),
            'email_from': os.getenv('EMAIL_FROM'),
            'email_password': os.getenv('EMAIL_PASSWORD'),
            'email_to': os.getenv('EMAIL_TO') or 'annaysergi@gmail.com',
            'instance_name': os.getenv(
                'INSTANCE_NAME',
                f'vps-auto-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
            ),
            'ocpu': int(os.getenv('OCPU', '2')),
            'memory_gb': int(os.getenv('MEMORY_GB', '12')),
        }
        if not config['compartment_ocid']:
            config['compartment_ocid'] = config['tenancy_ocid']

        required = ['user_ocid', 'tenancy_ocid', 'subnet_ocid', 'api_key_content', 'api_fingerprint']
        missing = [key for key in required if not config[key]]
        if missing:
            logger.error('Error: faltan secretos de GitHub: %s', ', '.join(missing))
            sys.exit(1)
        return config

    def create_oci_config(self):
        return {
            'user': self.config['user_ocid'],
            'key_content': self.config['api_key_content'],
            'fingerprint': self.config['api_fingerprint'],
            'tenancy': self.config['tenancy_ocid'],
            'region': self.config['region'],
        }

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def send_notification_email(self, instance_details):
        if not self.config['email_from'] or not self.config['email_password']:
            logger.warning('Configuracion de email incompleta. Saltando notificacion.')
            return False
        try:
            msg = MIMEMultipart()
            msg['From'] = self.config['email_from']
            msg['To'] = self.config['email_to']
            msg['Subject'] = 'Oracle VPS creada correctamente'
            body = f"""
Tu instancia de Oracle Cloud ha sido creada correctamente.

DETALLES DE LA INSTANCIA
- Nombre: {instance_details.get('display_name')}
- ID: {instance_details.get('id')}
- Estado: {instance_details.get('lifecycle_state')}
- Region: {self.config['region']}
- Availability Domain: {instance_details.get('availability_domain')}
- IP publica: {instance_details.get('public_ip', 'pendiente de asignacion')}

ESPECIFICACIONES
- Shape: {SHAPE}
- OCPUs: {self.config['ocpu']}
- Memoria: {self.config['memory_gb']} GB
"""
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(self.config['email_from'], self.config['email_password'])
                server.send_message(msg)
            logger.info('Email de notificacion enviado a %s', self.config['email_to'])
            return True
        except Exception as e:
            logger.error('Error enviando email: %s', e)
            return False

    # ------------------------------------------------------------------
    # Oracle Cloud
    # ------------------------------------------------------------------
    def get_availability_domains(self):
        identity_client = oci.identity.IdentityClient(self.create_oci_config())
        ads = identity_client.list_availability_domains(
            compartment_id=self.config['tenancy_ocid']
        ).data
        return sorted(ad.name for ad in ads)

    def get_free_tier_image(self, compute_client):
        """Busca la imagen Canonical Ubuntu 24.04 Minimal aarch64 mas reciente."""
        if self.image_id:
            return self.image_id

        manual_image_id = os.getenv('ORACLE_IMAGE_ID')
        if manual_image_id:
            logger.info('Usando image ID manual de los secretos.')
            self.image_id = manual_image_id
            return self.image_id

        logger.info('Buscando imagen Canonical Ubuntu 24.04 Minimal aarch64...')
        images = compute_client.list_images(
            compartment_id=self.config['tenancy_ocid'],
            operating_system='Canonical Ubuntu',
            shape=SHAPE,
            sort_by='TIMECREATED',
            sort_order='DESC'
        ).data
        if not images:
            raise RuntimeError(
                'No se encontro ninguna imagen de Canonical Ubuntu compatible con '
                f'{SHAPE} en esta region/compartment.'
            )

        def describe(image):
            return ' '.join(
                str(getattr(image, field, '') or '')
                for field in ('display_name', 'operating_system_version')
            ).lower()

        preferred = [
            img for img in images
            if '24.04' in describe(img) and 'minimal' in describe(img)
        ]
        ubuntu_2404 = [img for img in images if '24.04' in describe(img)]

        if preferred:
            chosen = preferred[0]
        elif ubuntu_2404:
            chosen = ubuntu_2404[0]
        else:
            chosen = images[0]

        self.image_id = chosen.id
        logger.info(
            'Imagen seleccionada: %s (%s)',
            getattr(chosen, 'display_name', 'sin nombre'),
            self.image_id
        )
        return self.image_id

    def get_ssh_key(self):
        return os.getenv('SSH_PUBLIC_KEY', '')

    def find_existing_instance(self, compute_client):
        """Devuelve una instancia A1.Flex viva ya existente, si la hay."""
        try:
            instances = compute_client.list_instances(
                compartment_id=self.config['compartment_ocid']
            ).data
        except oci.exceptions.ServiceError as e:
            logger.warning('No se pudo comprobar instancias existentes: %s', e.code)
            return None

        active_states = ('PROVISIONING', 'RUNNING', 'STARTING', 'STOPPING', 'STOPPED')
        for instance in instances:
            shape = getattr(instance, 'shape', '') or ''
            state = getattr(instance, 'lifecycle_state', '') or ''
            if shape == SHAPE and state in active_states:
                return instance
        return None

    def get_public_ip(self, compute_client, instance_id):
        try:
            attachments = compute_client.list_vnic_attachments(
                compartment_id=self.config['compartment_ocid'],
                instance_id=instance_id
            ).data
            for attachment in attachments:
                if attachment.lifecycle_state != 'ATTACHED':
                    continue
                vnic = compute_client.get_vnic(attachment.vnic_id).data
                if vnic.public_ip:
                    return vnic.public_ip
        except Exception as e:  # la IP es informativa: nunca debe romper el flujo
            logger.warning('No se pudo obtener la IP publica todavia: %s', e)
        return None

    def try_create_instance(self, availability_domain):
        compute_client = oci.core.ComputeClient(self.create_oci_config())
        try:
            instance_details = oci.core.models.LaunchInstanceDetails(
                availability_domain=availability_domain,
                display_name=self.config['instance_name'],
                compartment_id=self.config['compartment_ocid'],
                image_id=self.get_free_tier_image(compute_client),
                shape=SHAPE,
                subnet_id=self.config['subnet_ocid'],
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=self.config['ocpu'],
                    memory_in_gbs=self.config['memory_gb']
                ),
                metadata={'ssh_authorized_keys': self.get_ssh_key()}
            )
            instance = compute_client.launch_instance(instance_details).data
            logger.info('Instancia creada correctamente en %s', availability_domain)

            public_ip = self.get_public_ip(compute_client, instance.id)
            self.send_notification_email({
                'display_name': instance.display_name,
                'id': instance.id,
                'lifecycle_state': instance.lifecycle_state,
                'availability_domain': availability_domain,
                'public_ip': public_ip or 'pendiente de asignacion',
            })
            return True
        except oci.exceptions.ServiceError as e:
            # Siempre se registra el error real (codigo/estado/mensaje de OCI).
            logger.info(
                'Respuesta de OCI en %s -> code=%s status=%s mensaje="%s"',
                availability_domain, e.code, e.status, e.message
            )
            if is_out_of_capacity_error(e):
                return False
            raise RuntimeError(
                'Error real de OCI (credenciales, subnet, imagen, limites o permisos). '
                f'Codigo: {e.code}, Status: {e.status}, Mensaje: {e.message}'
            ) from e
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f'Error inesperado al crear la instancia: {e}') from e

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------
    def run(self):
        availability_domains = self.get_availability_domains()
        logger.info('Zonas disponibles: %s', ', '.join(availability_domains))
        if not availability_domains:
            raise RuntimeError('Oracle no devolvio ninguna availability domain para esta region.')

        setup_client = oci.core.ComputeClient(self.create_oci_config())
        existing = self.find_existing_instance(setup_client)
        if existing:
            logger.info(
                'Ya existe una instancia %s en estado %s (id %s). No se crea otra.',
                SHAPE, existing.lifecycle_state, existing.id
            )
            self.send_notification_email({
                'display_name': existing.display_name,
                'id': existing.id,
                'lifecycle_state': existing.lifecycle_state,
                'availability_domain': existing.availability_domain,
                'public_ip': self.get_public_ip(setup_client, existing.id) or 'pendiente de asignacion',
            })
            return 'SUCCESS'

        # La imagen se resuelve una sola vez, no en cada intento.
        self.get_free_tier_image(setup_client)

        deadline = time.monotonic() + MAX_RUNTIME_SECONDS
        attempt = 0
        capacity_failures = 0

        while time.monotonic() < deadline:
            ad = availability_domains[attempt % len(availability_domains)]
            attempt += 1
            logger.info('Intento %d en %s...', attempt, ad)
            if self.try_create_instance(ad):
                return 'SUCCESS'

            capacity_failures += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            wait = min(RETRY_DELAY_SECONDS, remaining)
            logger.warning(
                'Sin capacidad en %s. Reintentando en %ds (intentos con falta de capacidad: %d).',
                ad, int(wait), capacity_failures
            )
            time.sleep(wait)

        logger.warning(
            'Sin capacidad tras %d intentos repartidos entre las 3 ADs. '
            'Se reintentara en la siguiente ejecucion.', attempt
        )
        return 'NO_CAPACITY'


def main():
    try:
        result = OracleVPSProvisioner().run()
        if result == 'SUCCESS':
            logger.info('Provisioning completado. Fin.')
            sys.exit(0)
        if result == 'NO_CAPACITY':
            logger.warning(
                'No hay capacidad en Oracle Cloud (situacion esperada). '
                'Reintentaremos en la proxima ejecucion.'
            )
            sys.exit(0)
        logger.error('Estado desconocido: %s', result)
        sys.exit(1)
    except RuntimeError as e:
        logger.error('Error real detectado (no es falta de capacidad): %s', e)
        sys.exit(1)
    except Exception as e:
        logger.error('Error inesperado: %s', e)
        sys.exit(1)


if __name__ == '__main__':
    main()
