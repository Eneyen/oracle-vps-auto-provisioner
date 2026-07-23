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
        """Configuración fija y limpia extraída directamente de tu panel de Oracle"""
        config = {
            'user_ocid': 'ocid1.user.oc1.aaaaaaaafdkvhi477aynkvlkqbwryfmfptuqkutbspwfsj2m2sj5ziewtoq',
            'tenancy_ocid': 'ocid1.tenancy.oc1.aaaaaaaabl3kf4gb43gmqgani35n35nc35t7jfilhexuwvxnczkhlk3k1gq',
            'region': 'eu-frankfurt-1',
            'compartment_ocid': os.getenv('ORACLE_COMPARTMENT_OCID'),
            'subnet_ocid': os.getenv('ORACLE_SUBNET_OCID'),
            'api_key_path': './key.pem',
            'api_fingerprint': 'eb:65:2c:4f:5d:6f:c3:c0:11:b1:90:a3:36:13:54:14',
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

Hora de creación: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP_SSL('://gmail.com', 465) as server:
                server.login(self.config['email_from'], self.config['email_password'])
                server.send_message(msg)

            logger.info(f'Email de notificación enviado a {self.config["email_to"]}')
            return True
        except Exception as e:
            logger.error(f'Error enviando email: {e}')
            return False

    def try_create_instance(self):
        """Intenta crear la instancia de Oracle Cloud"""
        try:
            oci_config = self.create_oci_config()
            compute_client = oci.core.ComputeClient(oci_config)

            instance_details = oci.core.models.LaunchInstanceDetails(
                display_name=self.config['instance_name'],
                compartment_id=self.config['compartment_ocid'],
                image_id=self.get_free_tier_image(),
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
            if e.status == 400 and 'Out of capacity' in str(e):
                logger.warning(f'⚠️ Sin capacidad disponible en Frankfurt. Reintentando en {self.retry_delay}s...')
                return False
            else:
                logger.error(f'Error de OCI: {e}')
                return False
        except Exception as e:
            logger.error(f'Error inesperado: {e}')
            return False

    def get_free_tier_image(self):
        """Imagen gratuita de Ubuntu para Frankfurt (eu-frankfurt-1)"""
        return 'ocid1.image.oc1.eu-frankfurt-1.aaaaaaaamuep3y5bmsw3b7m6h77rshfuxrwt2yow3oaqf6exu3e4fexuhiq'

    def get_ssh_key(self):
        """Intenta cargar una clave SSH si está configurada"""
        return os.getenv('SSH_PUBLIC_KEY', '')

    def run_with_retries(self):
        """Ejecuta los intentos cíclicos"""
        for attempt in range(1, self.max_retries + 1):
            logger.info(f'Intento {attempt}/{self.max_retries}...')

            if self.try_create_instance():
                return True

            if attempt < self.max_retries:
                time.sleep(self.retry_delay)
        else:
            logger.error('❌ Se agotaron los intentos de esta tanda.')
            return False
        return False

def main():
    provisioner = OracleVPSProvisioner()
    provisioner.run_with_retries()

if __name__ == '__main__':
    main()
