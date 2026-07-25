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

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class OracleVPSProvisioner:
    def __init__(self):
        self.config = self.load_config()
        self.max_retries = 10
        self.retry_delay = 60  # segundos

    def load_config(self):
        """Configuración fija extraída directamente de tu panel de Oracle"""
        config = {
            'user_ocid': '***REMOVED-USER-OCID***',
            'tenancy_ocid': '***REMOVED-TENANCY-OCID***',
            'region': 'eu-frankfurt-1',
            'compartment_ocid': os.getenv('ORACLE_COMPARTMENT_OCID'),
            'subnet_ocid': os.getenv('ORACLE_SUBNET_OCID'),
            'api_key_path': './key.pem',
            'api_fingerprint': '***REMOVED-FINGERPRINT***',
            'email_from': os.getenv('EMAIL_FROM'),
            'email_password': os.getenv('EMAIL_PASSWORD'),
            'email_to': os.getenv('EMAIL_TO'),
            'instance_name': os.getenv('INSTANCE_NAME', f'vps-auto-{datetime.now().strftime("%Y%m%d-%H%M%S")}'),
            'ocpu': int(os.getenv('OCPU', '2')),
            'memory_gb': int(os.getenv('MEMORY_GB', '12')),
        }

        # Si el compartimento está vacío en los secretos, usamos el tenancy por defecto
        if not config['compartment_ocid']:
            config['compartment_ocid'] = config['tenancy_ocid']

        if not config['subnet_ocid']:
            logger.error("❌ Error: Falta configurar el secreto ORACLE_SUBNET_OCID en GitHub.")
            sys.exit(1)

        return config

    def create_oci_config(self):
        """Crea la configuración de OCI para la autenticación"""
        return {
            "user": self.config['user_ocid'],
            "key_file": self.config['api_key_path'],
            "fingerprint": self.config['api_fingerprint'],
            "tenancy": self.config['tenancy_ocid'],
            "region": self.config['region']
        }

    def send_notification_email(self, instance_details):
        """Envía email de notificación cuando se crea la instancia"""
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
        """Consulta las zonas (Availability Domains) disponibles en la region"""
        identity_client = oci.identity.IdentityClient(self.create_oci_config())
        ads = identity_client.list_availability_domains(compartment_id=self.config['tenancy_ocid']).data
        return [ad.name for ad in ads]

    def try_create_instance(self, availability_domain):
        """Intenta crear la instancia de Oracle Cloud"""
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
                metadata={
                    "ssh_authorized_keys": self.get_ssh_key()
                }
            )

            response = compute_client.launch_instance(instance_details)
            instance = response.data

            logger.info(f'✅ ¡Instancia creada exitosamente!')
            self.send_notification_email({
                'display_name': instance.display_name,
                'id': instance.id,
                'lifecycle_state': instance.lifecycle_state
            })
            return True

        except oci.exceptions.ServiceError as e:
            if 'Out of capacity' in str(e) or 'Out of host capacity' in str(e):
                logger.warning(f'⚠️ Sin capacidad disponible en {availability_domain}. Reintentando en {self.retry_delay}s...')
                return False
            else:
                logger.error(f'Error de OCI: {e}')
                return False
        except Exception as e:
            logger.error(f'Error inesperado: {e}')
            return False

    def get_free_tier_image(self, compute_client):
        """Busca la imagen de Ubuntu mas reciente compatible con VM.Standard.A1.Flex.
        Si el secreto ORACLE_IMAGE_ID esta definido, se usa ese en su lugar."""
        manual_image_id = os.getenv('ORACLE_IMAGE_ID')
        if manual_image_id:
            return manual_image_id

        images = compute_client.list_images(
            compartment_id=self.config['tenancy_ocid'],
            operating_system='Canonical Ubuntu',
            shape='VM.Standard.A1.Flex',
            sort_by='TIMECREATED',
            sort_order='DESC'
        ).data

        if not images:
            raise RuntimeError('No se encontro ninguna imagen de Ubuntu compatible con VM.Standard.A1.Flex')

        return images[0].id

    def get_ssh_key(self):
        """Intenta cargar una clave SSH si está configurada"""
        return os.getenv('SSH_PUBLIC_KEY', '')

    def run_with_retries(self):
        """Ejecuta los intentos cíclicos, rotando entre las zonas disponibles"""
        availability_domains = self.get_availability_domains()
        logger.info(f'Zonas disponibles: {", ".join(availability_domains)}')

        for attempt in range(1, self.max_retries + 1):
            ad = availability_domains[(attempt - 1) % len(availability_domains)]
            logger.info(f'Intento {attempt}/{self.max_retries} en {ad}...')

            if self.try_create_instance(ad):
                return True

            if attempt < self.max_retries:
                time.sleep(self.retry_delay)

        logger.error('❌ Se agotaron los intentos de esta tanda.')
        return False

def main():
    provisioner = OracleVPSProvisioner()
    provisioner.run_with_retries()

if __name__ == '__main__':
    main()
