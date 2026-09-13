export type AwsOnboardingInput = { accountName:string; accountId:string; region:string; certificateCommonName:string; caCertificatePem:string };

const slug=(value:string)=>value.trim().toLowerCase().replace(/[^a-z0-9-]+/g,"-").replace(/^-+|-+$/g,"").slice(0,40);
const pemBlock=(value:string)=>value.trim().split(/\r?\n/).map(line=>`            ${line}`).join("\n");

export function validateAwsOnboarding(input:AwsOnboardingInput):string|null {
  if(!input.accountName.trim()) return "Enter an account name.";
  if(!/^\d{12}$/.test(input.accountId)) return "AWS account ID must contain exactly 12 digits.";
  if(!/^[a-z]{2}(?:-gov)?-[a-z]+-\d$/.test(input.region)) return "Enter a valid AWS region such as us-east-1.";
  if(!/^[A-Za-z0-9._-]{1,64}$/.test(input.certificateCommonName)) return "Enter the exact collector certificate CN.";
  const pem=input.caCertificatePem.trim();
  if(!/^-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+\r?\n-----END CERTIFICATE-----$/.test(pem)) return "Paste one PEM-encoded public CA certificate with its BEGIN and END lines.";
  return null;
}

export function buildAwsOnboardingTemplate(input:AwsOnboardingInput):string {
  const error=validateAwsOnboarding(input); if(error) throw new Error(error);
  const name=slug(input.accountName)||"account";
  return `AWSTemplateFormatVersion: '2010-09-09'
Description: CloudScope Roles Anywhere onboarding for ${name}
Rules:
  TargetAccount:
    Assertions:
      - Assert: !Equals [!Ref AWS::AccountId, '${input.accountId}']
        AssertDescription: Deploy only in AWS account ${input.accountId}.
  TargetRegion:
    Assertions:
      - Assert: !Equals [!Ref AWS::Region, '${input.region}']
        AssertDescription: Deploy only in ${input.region}.
Resources:
  TrustAnchor:
    Type: AWS::RolesAnywhere::TrustAnchor
    Properties:
      Name: cloudscope-${name}-ca
      Enabled: true
      Source:
        SourceType: CERTIFICATE_BUNDLE
        SourceData:
          X509CertificateData: |-
${pemBlock(input.caCertificatePem)}
  AlertTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: cloudscope-${name}-cost-alerts
  CollectorRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: CloudScope-${name}-Collector
      MaxSessionDuration: 3600
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: {Service: rolesanywhere.amazonaws.com}
            Action: [sts:AssumeRole, sts:SetSourceIdentity, sts:TagSession]
            Condition:
              ArnEquals:
                aws:SourceArn: !GetAtt TrustAnchor.TrustAnchorArn
              StringEquals:
                aws:SourceAccount: !Ref AWS::AccountId
                aws:PrincipalTag/x509Subject/CN: '${input.certificateCommonName}'
      Policies:
        - PolicyName: CloudScopeCollector
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Sid: InventoryRead
                Effect: Allow
                Action: [ec2:Describe*, elasticfilesystem:Describe*, elasticloadbalancing:Describe*, fsx:Describe*, rds:Describe*, rds:ListTagsForResource, s3:GetBucketLocation, s3:GetBucketTagging, s3:ListAllMyBuckets, tag:GetResources, tag:GetTagKeys, tag:GetTagValues]
                Resource: '*'
              - Sid: MetricsRead
                Effect: Allow
                Action: [cloudwatch:GetMetricData, cloudwatch:GetMetricStatistics, cloudwatch:ListMetrics]
                Resource: '*'
              - Sid: PricingRead
                Effect: Allow
                Action: [pricing:DescribeServices, pricing:GetAttributeValues, pricing:GetProducts]
                Resource: '*'
              - Sid: AlertPublish
                Effect: Allow
                Action: sns:Publish
                Resource: !Ref AlertTopic
  CollectorProfile:
    Type: AWS::RolesAnywhere::Profile
    Properties:
      Name: cloudscope-${name}-collector
      Enabled: true
      DurationSeconds: 3600
      AcceptRoleSessionName: false
      RequireInstanceProperties: false
      RoleArns: [!GetAtt CollectorRole.Arn]
Outputs:
  AccountId: {Value: !Ref AWS::AccountId}
  Region: {Value: !Ref AWS::Region}
  TrustAnchorArn: {Value: !GetAtt TrustAnchor.TrustAnchorArn}
  TrustAnchorId: {Value: !Ref TrustAnchor}
  ProfileArn: {Value: !GetAtt CollectorProfile.ProfileArn}
  ProfileId: {Value: !GetAtt CollectorProfile.ProfileId}
  RoleArn: {Value: !GetAtt CollectorRole.Arn}
  AlertTopicArn: {Value: !Ref AlertTopic}
`;
}
