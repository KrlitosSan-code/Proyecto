def asunto_certificado(
    escritura
):
    return f"Certificado de tradición Escritura - {escritura}"

def cargar_cuerpo_certificado(
    escritura
):
    return f"""Estimado/a usuario/a,

Me dirijo a usted para informarle que se adjunta a la presente comunicación el Certificado de Tradición y Libertad, correspondiente a la escritura {escritura}. Con la expedición de este documento, 

se da por terminada formalmente la gestión de su escritura con nosotros.

Es de suma importancia que realice una revisión exhaustiva del contenido del certificado, validando que todos los datos coincidan exactamente con la escritura pública firmada.

Protocolo de subsanación de inconsistencias:

Errores de Registro: De encontrar inconsistencias, estas deberán subsanarse directamente ante la ORIP correspondiente, ya que ellos emiten el concepto y son los únicos facultados para modificar el documento.

Errores de Notaría: Si el error es por causa de la Notaría, deberá notificarnos de inmediato para revisar el caso y proceder con la subsanación correspondiente.

Reclamación de copias y Atención al Cliente: Las copias pueden ser reclamadas de manera presencial presentando la factura original o la cédula de ciudadanía de los intervinientes.

Contamos con un horario de atención de lunes a viernes, de 8:00 a.m. a 5:30 p.m. en jornada continua.

Agradecemos la confianza depositada en nuestro despacho.

Cordialmente,

Carlos Sanabria 

Beneficencia y Registro 
NOTARÍA 48 DEL CÍRCULO DE BOGOTÁ D.C 
Av. Caracas #70-75 
WhatsApp: 3014007936
correo: beneficenciayregistro@notaria48bogota.com"""

def asunto_recibo(
    escritura,
    nir
):
    return f"Proceso de registro Escritura {escritura} - Nir: {nir}"

def cargar_cuerpo_recibo(
    escritura
):
    return f"""Estimado(a) usuario(a),

De manera atenta remitimos el recibo de pago correspondiente a la Escritura del asunto de la Gobernación de Cundinamarca.

⚠️ Información Importante: Plazos y Pagos de Registro de la escritura: {escritura}

Para que el trámite de su escritura sea exitoso y evitar sobrecostos legales, por favor lea atentamente las siguientes condiciones de cumplimiento obligatorio:

​El motivo de este correo es informarle sobre el procedimiento obligatorio para el registro de su escritura. Con el fin de asegurar el éxito de su trámite y evitar intereses de mora o sanciones, es fundamental que comprenda que el proceso consta de dos pagos obligatorios que deben realizarse en orden consecutivo:

​1. Primer Pago (Gobernación)
Debe realizar el pago correspondiente al Impuesto de Registro ante la Gobernación de Cundinamarca. Puede hacerlo de dos formas:

​- En línea: A través de https://gevir.cundinamarca.gov.co/consultas.php (https://gevir.cundinamarca.gov.co/consultas.php). Al ingresar el número de liquidación (ubicado en la parte superior derecha del recibo adjunto), debe omitir los ceros iniciales.
- Presencial: En los bancos autorizados con el recibo adjunto.

2. Notificación y Segundo Pago (Derechos de Registro)
Una vez que haya realizado el pago del paso anterior, es indispensable que nos envíe el comprobante de pago inmediatamente respondiendo a este correo o comunicándose al 601 8088139 ext. 106. Solo tras recibir esta notificación, podremos expedir y enviarle el segundo recibo correspondiente a los Derechos de Registro (Vur / Notariado). El trámite no se iniciará hasta que se completen ambos pagos.

Información Importante sobre Plazos:
Le recordamos que cuenta con un plazo máximo de dos (2) meses calendario, contados a partir de la fecha de la firma de la escritura, para radicar el documento. El tiempo corre desde la firma en la Notaría, no desde la generación de los recibos.

3. Instrucciones para la Liquidación y Pago:
Para consultar el estado de su liquidación o realizar el pago en línea:
Número de Liquidación: Lo encontrará en la parte superior derecha de su recibo.
Formato de búsqueda: Al digitar el número en el portal de la Gobernación, debe omitir los ceros iniciales. (Ejemplo: Si el número es 000012345, digite solo 12345).

¿Por qué esto es vital?

Muchos usuarios asumen que el plazo comienza cuando reciben el recibo, pero la ley es clara: el tiempo corre desde que se firmó la escritura en la Notaría. Le recomendamos realizar ambos pagos de manera inmediata para evitar que el sistema bloquee el trámite por extemporaneidad.

En línea: Puede realizar el pago a través del portal de Trámites de Cundinamarca en el siguiente enlace: https://gevir.cundinamarca.gov.co/consultas.php, utilizando el número de liquidación mencionado.
Presencial: Si prefiere realizar el pago de manera presencial, puede dirigirse a cualquier oficina de la Gobernación de Cundinamarca o a los bancos autorizados, presentando el recibo adjunto en el banco correspondiente.

Le solicitamos validar que toda la información en la liquidación sea correcta. En caso de encontrar alguna inconsistencia, por favor contáctenos inmediatamente antes de efectuar el pago.

Agradecemos su atención a estas instrucciones para garantizar la agilidad de su trámite.

Cordialmente,

Beneficencia y Registro
NOTARÍA 48 DEL CÍRCULO DE BOGOTÁ D.C.

"""