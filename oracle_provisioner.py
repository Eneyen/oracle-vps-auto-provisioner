#!/usr/bin/env python3
"""
Oracle VPS Auto Provisioner - Versión Definitiva 2026
"""

import os
import sys
import time
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import oci
from datetime import datetime
import logging

# Configurar logging a archivo y stdout
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
    """Detecta si un ServiceError es por falta de capacidad usando campos estructurados."""
    if not hasattr(service_error, 'code') or not hasattr(service_error, 'message'):
        return False

    code = str(service_error.code).strip().lower() if service_error.code else ''
    status = getattr(service_error, 'status', None)
    raw_message = str(service_error.message) if service_error.message else ''
    message = ' '.join(raw_message.lower().split())
    normalized_message = message.rstrip(' .:;!?')

    capacity_codes = ('outhostcapacity', 'outhostcapacityexception', 'outofhostcapacity')
    if code in capacity_codes:
        return True

    # Caso real observado en OCI Frankfurt:
    # code=InternalError, status=500, message='Out of host capacity.'
    if code == 'internalerror' and status == 500:
        if normalized_message in ('out of host capacity', 'out of capacity'):
            return True

    if code:
        return False

    if 'out of capacity' in message or 'out of host capacity' in message:
        return True

    return False


class OracleVPSProvisioner:
    def __init__(self):
        self.config = self.load_config()
        self.max_retries = 10
        self.retry_delay = 60

    def load_config(self):
        config = {
            'user_ocid': os.getenv('ORACLE_USER_OCID'),
            'tenancy_ocid': os.getenv('ORACLE_TENANCY_OCID'),
            'region': 'eu-frankfurt-1',
            'compartment_ocid': os.getenv('ORACLE_COMPARTMENT_OCID'),
            'subnet_ocid': os.getenv('ORACLE_SUBNET_OCID'),
            'api_key_content': os.getenv('ORACLE_API_KEY'),
            'api_fingerprint': os.getenv('ORACLE_API_FINGERPRINT'),
            'email_from': os.getenv('EMAIL_FROM'),
            'email_password': os.getenv('EMAIL_PASSWORD'),
            'email_to': os.getenv('EMAIL_TO'),
            'instance_name': os.getenv('INSTANCE_NAME', f'vps-auto-{datetime.now().strftime("%Y%m%d-%H%M%S")}'),
            'ocpu': int(os.getenv('OCPU', '2')),
            'memory_gb': int(os.getenv('MEMORY_GB', '12')),
        }
        if not config['compartment_ocid']:
            config['compartment_ocid'] = config['tenancy_ocid']
        required = ['user_ocid', 'tenancy_ocid', 'subnet_ocid', 'api_key_content', 'api_fingerprint']
        missing = [key for key in required if not config[key]]
        if missing:
            logger.error(f"❌ Error: Faltan secretos de GitHub: {', '.join(missing)}")
            sys.exit(1)
        return config

    def create_oci_config(self):
        return {
            "user": self.config['user_ocid'],
            "key_content": self.config['api_key_content'],
            "fingerprint": self.config['api_fingerprint'],
            "tenancy": self.config['tenancy_ocid'],
            "region": self.config['region']
        }

    def send_notification_email(self, instance_details):
        try:
            if not self.config['email_from'] or not self.config['email_password']:
                logger.warning('⚠️ Configuración de email incompleta. Saltando notificación.')
                return False
            msg = MIMEMultipart()
            msg['From'] = self.config['email_from']
            msg['To'] = self.config['email_to']
            msg['Subject'] = '✅ Oracle VPS creada exitosamente!'
            body = f"""
¡Éxito! Tu instancia de Oracle VPS ha sido creada.

DETALLES DE LA INSTANCIA:
- Nombre: {instance_details['display_name']}
- ID: {instance_details['id']}
- Estado: {instance_details['lifecycle_state']}
- Region: {self.config['region']}
- IP Pública: {instance_details.get('public_ip', 'En asignación...')}

ESPECIFICACIONES:
- OCPUs: {self.config['ocpu']}
- Memoria: {self.config['memory_gb']}GB
"""
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.config['email_from'], self.config['email_password'])
                server.send_message(msg)
            logger.info(f'Email de notificación enviado a {self.config["email_to"]}')
            return True
        except Exception as e:
            logger.error(f'Error enviando email: {e}')
            return False

    def get_availability_domains(self):
        identity_client = oci.identity.IdentityClient(self.create_oci_config())
        ads = identity_client.list_availability_domains(compartment_id=self.config['tenancy_ocid']).data
        return [ad.name for ad in ads]

    def try_create_instance(self, availability_domain):
        try:
            oci_config = self.create_oci_config()
            compute_client = oci.core.ComputeClient(oci_config)
            instance_details = oci.core.models.LaunchInstanceDetails(
                availability_domain=availability_domain,
                display_name=self.config['instance_name'],
                compartment_id=self.config['compartment_ocid'],
                image_id=self.get_free_tier_image(compute_client),
                shape="VM.Standard.A1.Flex",
                subnet_id=self.config['subnet_ocid'],
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=self.config['ocpu'],
                    memory_in_gbs=self.config['memory_gb']
                ),
                metadata={"ssh_authorized_keys": self.get_ssh_key()}
            )
            response = compute_client.launch_instance(instance_details)
            instance = response.data
            logger.info('✅ ¡Instancia creada exitosamente!')
            self.send_notification_email({
                'display_name': instance.display_name,
                'id': instance.id,
                'lifecycle_state': instance.lifecycle_state
            })
            return True
        except oci.exceptions.ServiceError as e:
            if is_out_of_capacity_error(e):
                logger.warning(f'⚠️ Sin capacidad disponible en {availability_domain}. Reintentando en {self.retry_delay}s...')
                return False
            raise RuntimeError(
                f'Error de OCI (credenciales, subnet, image, permisos, etc.). Código: {e.code}, Status: {e.status}, Mensaje: {e.message}'
            ) from e
        except oci.exceptions.AuthenticationError as e:
            raise RuntimeError(f'Error de autenticación con Oracle Cloud: {e}') from e
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f'Error inesperado al crear instancia: {e}') from e

    def get_free_tier_image(self, compute_client):
        manual_image_id = os.getenv('ORACLE_IMAGE_ID')
        if manual_image_id:
            logger.info(f'Usando image ID manual: {manual_image_id}')
            return manual_image_id
        logger.info('Buscando imagen de Ubuntu mas reciente para VM.Standard.A1.Flex...')
        images = compute_client.list_images(
            compartment_id=self.config['tenancy_ocid'],
            operating_system='Canonical Ubuntu',
            shape='VM.Standard.A1.Flex',
            sort_by='TIMECREATED',
            sort_order='DESC'
        ).data
        if not images:
            raise RuntimeError('No se encontro ninguna imagen de Ubuntu compatible con VM.Standard.A1.Flex en esta compartment')
        image_id = images[0].id
        logger.info(f'Image encontrada: {image_id}')
        return image_id

    def get_ssh_key(self):
        return os.getenv('SSH_PUBLIC_KEY', '')

    def run_with_retries(self):
        availability_domains = self.get_availability_domains()
        logger.info(f'Zonas disponibles: {", ".join(availability_domains)}')
        for attempt in range(1, self.max_retries + 1):
            ad = availability_domains[(attempt - 1) % len(availability_domains)]
            logger.info(f'Intento {attempt}/{self.max_retries} en {ad}...')
            if self.try_create_instance(ad):
                return 'SUCCESS'
            if attempt < self.max_retries:
                time.sleep(self.retry_delay)
        logger.warning('⚠️ Se agotaron los intentos. No hay capacidad disponible en ninguna zona.')
        return 'NO_CAPACITY'

def main():
    try:
        provisioner = OracleVPSProvisioner()
        result = provisioner.run_with_retries()
        if result == 'SUCCESS':
            logger.info('✅ Provisioning completado exitosamente.')
            sys.exit(0)
        elif result == 'NO_CAPACITY':
            logger.warning('⚠️ No hay capacidad en Oracle Cloud (situación esperada). Reintentaremos en la próxima ejecución.')
            sys.exit(0)
        logger.error(f'Estado desconocido: {result}')
        sys.exit(1)
    except RuntimeError as e:
        logger.error(f'❌ Error real detectado (no es falta de capacidad): {e}')
        sys.exit(1)
    except Exception as e:
        logger.error(f'❌ Error inesperado: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()
